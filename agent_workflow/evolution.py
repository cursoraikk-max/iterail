"""Foreground, owner-bound release supervisor. Native fixtures are not model trials."""
import argparse
import contextlib
import copy
import fcntl
import fnmatch
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import uuid


class EvolutionError(ValueError):
    pass


def require(value, message):
    if not value:
        raise EvolutionError(message)


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False).encode()


def canonical(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def file_sha(path):
    path = Path(path)
    require(not path.is_symlink() and path.is_file(), 'trusted regular file missing')
    h = hashlib.sha256()
    with path.open('rb') as stream:
        while chunk := stream.read(65536): h.update(chunk)
    return h.hexdigest()


def source_digest(root):
    root = Path(root).resolve(); h = hashlib.sha256()
    for path in sorted(root.rglob('*')):
        rel = path.relative_to(root)
        if any(x in {'.git', '.workflow', '__pycache__'} for x in rel.parts) or path.suffix == '.pyc':
            continue
        require(not path.is_symlink(), 'source symlink')
        if path.is_file():
            h.update(str(rel).encode() + b'\0')
            with path.open('rb') as stream:
                while chunk := stream.read(65536): h.update(chunk)
            h.update(b'\0')
    return h.hexdigest()


def manifest(root):
    root = Path(root).resolve()
    return {str(p.relative_to(root)): file_sha(p) for p in sorted(root.rglob('*'))
            if p.is_file() and not any(x in {'.git', '__pycache__'} for x in p.relative_to(root).parts)
            and p.suffix != '.pyc'}


def git(root, *args):
    p = subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=True, timeout=30)
    require(p.returncode == 0, 'git: ' + p.stderr.strip())
    return p.stdout.strip()


def safe_path(value):
    require(isinstance(value, str) and value and '\\' not in value and '\0' not in value,
            'relative source path required')
    p = PurePosixPath(value)
    require(not p.is_absolute() and str(p) == value and all(x not in {'.', '..'} for x in p.parts),
            'unsafe relative source path')
    return value


def protected(path):
    return path.startswith(('tests/', 'skills/', 'project.', 'agent_workflow/evolution',
                            'agent_workflow/governor', 'agent_workflow/guardian'))


def load_catalog(config):
    path = config['strategy_catalog']; sha = config['strategy_catalog_sha256']
    if path is None:
        require(sha is None, 'catalog hash without catalog')
        return None
    require(isinstance(path, str) and Path(path).is_absolute() and file_sha(path) == sha,
            'owner catalog SHA mismatch')
    p = json.loads(Path(path).read_text())
    require(isinstance(p, dict) and set(p) == {'schema_version', 'bounds', 'selected_id', 'approved_profiles'}
            and p['schema_version'] == 1, 'invalid catalog schema')
    bounds = p['bounds']
    require(isinstance(bounds, dict) and set(bounds) == {'context_max_bytes', 'retry_summary_bytes'}
            and type(bounds['context_max_bytes']) is int and 0 < bounds['context_max_bytes'] <= 65536
            and type(bounds['retry_summary_bytes']) is int and 0 < bounds['retry_summary_bytes'] <= 16384,
            'invalid catalog bounds')
    require(isinstance(p['approved_profiles'], list) and len(p['approved_profiles']) <= 32,
            'bounded approved profiles required')
    ids = set(); selected = None
    for profile in p['approved_profiles']:
        require(isinstance(profile, dict) and set(profile) == {'id', 'hypothesis', 'context', 'prompt'},
                'invalid strategy proposal fields')
        require(isinstance(profile['id'], str) and profile['id'] and profile['id'] not in ids
                and isinstance(profile['hypothesis'], str) and 0 < len(profile['hypothesis']) <= 4096,
                'strategy ID/hypothesis required')
        ids.add(profile['id']); ctx = profile['context']; prompt = profile['prompt']
        require(isinstance(ctx, dict) and set(ctx) == {'max_bytes', 'mandatory_paths', 'query'}
                and type(ctx['max_bytes']) is int and 0 < ctx['max_bytes'] <= bounds['context_max_bytes']
                and isinstance(ctx['query'], str) and len(ctx['query'].encode()) <= 2048
                and isinstance(ctx['mandatory_paths'], list) and len(ctx['mandatory_paths']) <= 64,
                'invalid bounded strategy context')
        for x in ctx['mandatory_paths']: safe_path(x)
        require(len(set(ctx['mandatory_paths'])) == len(ctx['mandatory_paths']), 'duplicate mandatory path')
        require(isinstance(prompt, dict) and set(prompt) == {'retry_summary_bytes', 'include_related'}
                and type(prompt['retry_summary_bytes']) is int
                and 0 <= prompt['retry_summary_bytes'] <= bounds['retry_summary_bytes']
                and type(prompt['include_related']) is bool, 'invalid bounded strategy prompt')
        if profile['id'] == p['selected_id']: selected = copy.deepcopy(profile)
    require(p['selected_id'] is None or selected is not None, 'unapproved selected strategy')
    return selected


def validate_config(config):
    keys = {'schema_version', 'project', 'repo', 'backend', 'backend_adapter', 'checks', 'tests',
            'health', 'allowlist', 'budget', 'poll_seconds', 'self_update', 'idle', 'breakers',
            'execution_mode', 'provider_authorized', 'strategy_catalog', 'strategy_catalog_sha256'}
    require(isinstance(config, dict) and set(config) == keys, 'exact owner configuration required')
    c = copy.deepcopy(config)
    require(type(c['schema_version']) is int and c['schema_version'] == 1, 'owner schema version')
    project_optional = {'purpose', 'goals', 'non_goals', 'needs', 'maintenance', 'uiux'}
    require(isinstance(c['project'], dict) and {'id', 'kind'} <= set(c['project'])
            and not set(c['project'])-{'id', 'kind'}-project_optional
            and isinstance(c['project']['id'], str) and c['project']['id']
            and c['project']['kind'] in {'target', 'controller'}, 'invalid project specification')
    for key in ('purpose',):
        if key in c['project']: require(isinstance(c['project'][key], str) and len(c['project'][key]) <= 4096, 'bounded project purpose required')
    for key in ('goals', 'non_goals'):
        if key in c['project']:
            require(isinstance(c['project'][key], list) and len(c['project'][key]) <= 64
                    and all(isinstance(x, str) and 0 < len(x) <= 4096 for x in c['project'][key]), 'bounded project metadata required')
    if 'needs' in c['project']:
        needs = c['project']['needs']
        require(isinstance(needs, list) and len(needs) <= 64, 'bounded capability needs required')
        ids = set()
        for need in needs:
            require(isinstance(need, dict) and set(need) == {'id','kind','name','current'}
                    and need['kind'] in {'dependency','capability'}
                    and all(isinstance(need[x], str) and 0 < len(need[x]) <= 4096 for x in ('id','name','current'))
                    and need['id'] not in ids, 'invalid project capability need')
            ids.add(need['id'])
    maintenance = c['project'].get('maintenance')
    uiux = c['project'].get('uiux')
    if uiux is not None:
        from .uiux import validate_policy as validate_uiux, strict_json as uiux_json, read_regular as uiux_read
        require(isinstance(uiux, dict) and set(uiux) == {'path', 'sha256'}
                and isinstance(uiux['path'], str) and Path(uiux['path']).is_absolute()
                and not Path(uiux['path']).is_symlink()
                and not Path(uiux['path']).resolve().is_relative_to(Path(c['repo']).resolve())
                and re.fullmatch('[0-9a-f]{64}', str(uiux['sha256'])) is not None, 'owner UIUX policy reference invalid')
        raw = uiux_read(Path(uiux['path']), 1048576)
        require(hashlib.sha256(raw).hexdigest() == uiux['sha256'], 'owner UIUX policy SHA changed')
        plan = validate_uiux(uiux_json(raw))
        require(plan['project']['id'] == c['project']['id'], 'UIUX project identity mismatch')
    if maintenance is not None:
        require(isinstance(maintenance, dict) and set(maintenance) == {'config', 'sha256'}
                and isinstance(maintenance['config'], str) and Path(maintenance['config']).is_absolute()
                and not Path(maintenance['config']).resolve().is_relative_to(Path(c['repo']).resolve())
                and re.fullmatch('[0-9a-f]{64}', str(maintenance['sha256'])) is not None
                and file_sha(maintenance['config']) == maintenance['sha256'], 'owner maintenance reference invalid')
        from .maintenance import validate_config as validate_maintenance, strict_json
        plan = validate_maintenance(strict_json(Path(maintenance['config']).read_bytes()))
        fields = ('id','purpose','goals','non_goals','needs')
        require(all(x in c['project'] for x in fields)
                and plan['project'] == {x: c['project'][x] for x in fields}, 'maintenance project identity or goals mismatch')
    for name in ('repo', 'backend'):
        require(isinstance(c[name], str) and Path(c[name]).is_absolute() and not Path(c[name]).is_symlink(),
                'absolute local ' + name + ' required')
        c[name] = str(Path(c[name]).resolve())
    require((Path(c['repo']) / '.git').is_dir(), 'independent local project Git checkout required')
    require(Path(c['backend']).is_file() and os.access(c['backend'], os.X_OK), 'executable backend required')
    adapter = c['backend_adapter']
    require(isinstance(adapter, dict) and set(adapter) == {'kind', 'argv', 'protocol'}
            and adapter['kind'] in {'command', 'codex_exec'} and adapter['protocol'] == 'exec-v1'
            and isinstance(adapter['argv'], list) and all(isinstance(x, str) and x for x in adapter['argv']),
            'invalid backend adapter')
    if adapter['kind'] == 'command':
        require(adapter['argv'] and str(Path(adapter['argv'][0]).resolve()) == c['backend'], 'adapter executable mismatch')
        for arg in adapter['argv']:
            require(set(re.findall(r'\{([^{}]+)\}', arg)) <= {'kind', 'repo', 'slot', 'prompt', 'verdict'},
                    'unknown adapter placeholder')
    else: require(adapter['argv'] == [], 'fixed Codex adapter requires empty argv')
    require(c['execution_mode'] in {'native_fixture', 'provider'} and type(c['provider_authorized']) is bool,
            'explicit execution mode required')
    require(c['execution_mode'] != 'provider' or c['provider_authorized'], 'provider requires a new owner grant')
    require(c['execution_mode'] != 'native_fixture' or adapter['kind'] == 'command', 'native fixture requires command adapter')
    b = c['budget']
    require(isinstance(b, dict) and set(b) == {'wall_seconds', 'max_calls', 'call_seconds', 'log_bytes', 'max_attempts'}
            and all(type(x) is int and x > 0 for x in b.values()) and b['call_seconds'] <= b['wall_seconds'],
            'invalid owner budget')
    require(type(c['poll_seconds']) is int and c['poll_seconds'] > 0, 'invalid poll duration')
    require(isinstance(c['allowlist'], list) and c['allowlist'] and isinstance(c['self_update'], list),
            'explicit source permissions required')
    for path in c['allowlist'] + c['self_update']:
        safe_path(path)
        require(path not in {'*', '**'} and not protected(path), 'protected owner path: ' + path)
    require(all(not any(x in path for x in '*?[') and any(fnmatch.fnmatchcase(path, glob) for glob in c['allowlist'])
                for path in c['self_update']), 'self-update must name explicit allowlisted files')
    require(c['project']['kind'] != 'target' or not c['self_update'], 'target cannot change controller authority')
    require(c['project']['kind'] != 'controller' or (Path(c['repo']) / 'agent_workflow/runner.py').is_file(),
            'controller project package missing')
    require(isinstance(c['checks'], list) and c['checks'], 'trusted checks required')
    ids = set()
    for check in c['checks']:
        require(isinstance(check, dict) and set(check) == {'id', 'argv', 'repair', 'finding_contract'}
                and isinstance(check['id'], str) and check['id'] and check['id'] not in ids
                and isinstance(check['repair'], str) and check['finding_contract'] == 'strict-v1', 'invalid check')
        ids.add(check['id'])
    for collection in (c['tests'], c['health']):
        require(isinstance(collection, list) and collection, 'trusted tests and health required')
    for argv in c['tests'] + c['health'] + [x['argv'] for x in c['checks']]:
        require(isinstance(argv, list) and argv and all(isinstance(x, str) and x for x in argv), 'nonempty trusted argv required')
        for arg in argv:
            p = Path(arg)
            require(not (p.is_absolute() and p.exists() and p.resolve().is_relative_to(Path(c['repo']))),
                    'trusted command inside editable project')
    idle = c['idle']
    require(isinstance(idle, dict) and set(idle) == {'initial_seconds', 'max_seconds'}
            and all(type(x) in (int, float) and x > 0 for x in idle.values())
            and idle['initial_seconds'] <= idle['max_seconds'], 'bounded idle configuration required')
    require(c['breakers'] == {'same_class_failures': 2, 'no_gain_cycles': 2}, 'fixed breakers required')
    if c['strategy_catalog'] is not None:
        require(not Path(c['strategy_catalog']).resolve().is_relative_to(Path(c['repo'])), 'catalog outside project required')
    load_catalog(c)
    return c


def derive_child_config(owner):
    fields = ('repo', 'backend', 'checks', 'tests', 'budget', 'allowlist', 'poll_seconds',
              'project', 'backend_adapter', 'self_update')
    return dict({x: copy.deepcopy(owner[x]) for x in fields}, strategy=load_catalog(owner))


class Governor:
    """One durable owner epoch; reservations are never refunded after a crash."""
    def __init__(self, state, config=None):
        raw_root = Path(state)
        require(not raw_root.is_symlink(), 'owner state symlink')
        existed = raw_root.exists(); self.root = raw_root.resolve()
        if not (self.root / 'evolution.sqlite').exists():
            require(not existed, 'initialized owner ledger missing; a new grant is required')
            require(config is not None, 'initialize owner first')
            c = validate_config(config)
            require(not git(c['repo'], 'status', '--porcelain'), 'commit reviewed project before owner init')
            self.root.mkdir(parents=True, mode=0o700)
        self.db = sqlite3.connect(self.root / 'evolution.sqlite', isolation_level=None, timeout=10, check_same_thread=False)
        self.db.row_factory = sqlite3.Row; self.mutex = threading.RLock(); self._held = False
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS meta(id INTEGER PRIMARY KEY CHECK(id=1),config TEXT,config_sha TEXT,stable_path TEXT,stable_sha TEXT,started REAL,deadline REAL,stopped INTEGER,blocked TEXT,active TEXT,epoch INTEGER,calls INTEGER,stable_manifest TEXT,trusted TEXT,reserved_seconds REAL,reserved_bytes INTEGER,failure_class TEXT,failure_count INTEGER,idle_count INTEGER);
        CREATE TABLE IF NOT EXISTS releases(id TEXT PRIMARY KEY,path TEXT,fingerprint TEXT,source TEXT,head TEXT,reviewed INTEGER,proof TEXT);
        CREATE TABLE IF NOT EXISTS reservations(id TEXT PRIMARY KEY,kind TEXT,job TEXT,seconds REAL,calls INTEGER,phase TEXT,release_id TEXT,active_epoch INTEGER,child_state TEXT,child_config_sha TEXT,check_id TEXT,created REAL,result TEXT);
        CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,at REAL,kind TEXT,data TEXT);
        CREATE TABLE IF NOT EXISTS activation(id TEXT PRIMARY KEY,expected TEXT,epoch INTEGER,candidate TEXT,head TEXT,source TEXT,phase TEXT);
        CREATE TABLE IF NOT EXISTS completions(job TEXT PRIMARY KEY,release_id TEXT,epoch INTEGER,child_state TEXT,result TEXT,phase TEXT);
        ''')
        row = self.db.execute('SELECT * FROM meta').fetchone()
        if row is None:
            require(not existed and config is not None, 'initialized owner history missing')
            self.config = c
            shutil.copytree(Path(__file__).parent, self.root / 'stable/agent_workflow', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            launcher = ('import sys\nfrom pathlib import Path\n'
                        'sys.dont_write_bytecode=True\n'
                        'sys.path.insert(0,str(Path(__file__).resolve().parent/"stable"))\n'
                        'from agent_workflow.evolution import main\nraise SystemExit(main())\n')
            (self.root/'start.py').write_text(launcher)
            # Both canonical entrypoint and dependency closure are owner-pinned.
            (self.root/'stable/entrypoint.py').write_text(launcher)
            stable = self.root / 'stable/agent_workflow/evolution.py'; started = time.time()
            trusted = self.trusted_files(c)
            release = self._snapshot(Path(c['repo']) if c['project']['kind'] == 'controller' else Path(__file__).parent.parent, bootstrap=True)
            self.db.execute('INSERT INTO meta VALUES(1,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (encoded(c).decode(), canonical(c), str(stable), file_sha(stable), started, started + c['budget']['wall_seconds'],
                 0, None, release['id'], 0, 0, encoded(manifest(self.root/'stable')).decode(), encoded(trusted).decode(), 0, 0, None, 0, 0))
            self.event('initialized', {'active': release['id'], 'evidence_kind': c['execution_mode'], 'external_provider_calls': 0})
        else:
            self.config = json.loads(row['config'])
            require(config is None or validate_config(config) == self.config, 'owner policy is immutable')
        self.integrity()

    @staticmethod
    def trusted_files(config):
        result = {}
        for item in (sys.executable, config['backend'], config['strategy_catalog']):
            if item: result[str(Path(item).resolve())] = file_sha(Path(item).resolve())
        maintenance = config['project'].get('maintenance')
        if maintenance: result[str(Path(maintenance['config']).resolve())] = file_sha(maintenance['config'])
        uiux = config['project'].get('uiux')
        if uiux: result[str(Path(uiux['path']).resolve())] = file_sha(uiux['path'])
        for argv in config['tests'] + config['health'] + [x['argv'] for x in config['checks']]:
            for arg in argv:
                p = Path(arg)
                if p.is_absolute() and p.is_file():
                    result[str(p.resolve())] = file_sha(p.resolve())
                    if p.suffix == '.py':
                        for helper in p.parent.glob('*.py'): result[str(helper.resolve())] = file_sha(helper.resolve())
        return result

    def _snapshot(self, source, bootstrap=False, proof=None):
        source = Path(source).resolve(); expected = source_digest(source)
        ident = 'release-' + expected[:16]; path = self.root / 'releases' / ident
        require(not path.exists(), 'duplicate release snapshot')
        path.parent.mkdir(exist_ok=True)
        if (source / '.git').is_dir() and not git(source, 'status', '--porcelain'):
            git(source, 'clone', '--no-local', str(source), str(path))
        else:
            # A target uses the tool package, never imports controller code from its project.
            require(bootstrap and self.config['project']['kind'] == 'target', 'release source must be committed')
            path.mkdir(); shutil.copytree(source / 'agent_workflow', path / 'agent_workflow', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            git(path, 'init', '-q'); git(path, 'add', '-A')
            git(path, '-c', 'user.name=Owner bootstrap', '-c', 'user.email=local@invalid', '-c', 'commit.gpgsign=false', 'commit', '-qm', 'Fixed tool package bootstrap')
        require(source_digest(source) == expected, 'release source changed during copy')
        actual = source_digest(path)
        if self.config['project']['kind'] == 'controller': require(actual == expected, 'release copy mismatch')
        data = {'id': ident, 'path': str(path), 'fingerprint': source_digest(path/'agent_workflow'),
                'source': actual, 'head': git(path, 'rev-parse', 'HEAD'), 'reviewed': int(bootstrap or proof is not None),
                'proof': encoded(proof or {'bootstrap': True, 'manifest': manifest(path)}).decode()}
        self.db.execute('INSERT INTO releases VALUES(?,?,?,?,?,?,?)', tuple(data[x] for x in ('id','path','fingerprint','source','head','reviewed','proof')))
        return data

    @contextlib.contextmanager
    def tx(self):
        with self.mutex:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                yield
                self.db.execute('COMMIT')
            except BaseException:
                self.db.execute('ROLLBACK'); raise

    @contextlib.contextmanager
    def locked(self):
        if self._held:
            yield; return
        with (self.root/'supervisor.lock').open('a') as lock:
            try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError: raise EvolutionError('foreground supervisor already running')
            self._held = True
            try: yield
            finally: self._held = False; fcntl.flock(lock, fcntl.LOCK_UN)

    def event(self, kind, data=None):
        self.db.execute('INSERT INTO events(at,kind,data) VALUES(?,?,?)', (time.time(), kind, encoded(data or {}).decode()))

    def status(self):
        meta = dict(self.db.execute('SELECT * FROM meta').fetchone())
        reservations = [dict(x) for x in self.db.execute('SELECT * FROM reservations ORDER BY rowid')]
        return {'meta': meta, 'reservations': reservations, 'accounting': self._accounting(meta, reservations),
                'events': [dict(x) for x in self.db.execute('SELECT * FROM events ORDER BY seq')],
                'releases': [dict(x) for x in self.db.execute('SELECT * FROM releases ORDER BY rowid')]}

    def _accounting(self, meta, reservations):
        """Observe confirmed launches and hash-bound protocol, never charged slots.

        Missing or truncated logs cannot prove zero model activity or a completed
        turn. Those observations are retained as unknown, without changing state.
        """
        from .receipt import normalize_exec_events
        counts = {'actual_backend_launches': 0, 'successful_model_turns': 0, 'reviewer_launches': 0,
                  'unexecuted_reviewer_reservations': 0, 'unknown_reviewer_reservations': 0,
                  'unknown_backend_launches': 0, 'unknown_model_turns': 0}
        for row in reservations:
            try:
                facts = json.loads(row['result'] or '{}')
                require(isinstance(facts, dict), 'reservation observation malformed')
            except (ValueError, TypeError):
                if row['kind'].startswith('supervised:backend:'): counts['unknown_backend_launches'] += 1
                if row['kind'] == 'attempt': counts['unknown_reviewer_reservations'] += 1
                continue
            if row['kind'] == 'attempt' and row['calls'] == 2:
                kinds = facts.get('backend_kinds');used = facts.get('actual_used')
                if (row['phase'] == 'finished' and type(used) is int and 0 <= used <= 2
                        and kinds == ['backend:worker', 'backend:reviewer'][:used]):
                    if 'backend:reviewer' not in kinds: counts['unexecuted_reviewer_reservations'] += 1
                else: counts['unknown_reviewer_reservations'] += 1
            if not row['kind'].startswith('supervised:backend:'): continue
            terminal = facts.get('terminal');call = facts.get('runner_call')
            if (not isinstance(terminal, dict) or not isinstance(call, str) or terminal.get('call_id') != call
                    or type(terminal.get('child_pid')) is not int or terminal['child_pid'] <= 0
                    or type(terminal.get('guardian_pid')) is not int or terminal['guardian_pid'] <= 0):
                counts['unknown_backend_launches'] += 1;counts['unknown_model_turns'] += 1;continue
            try:
                state = Path(row['child_state'])
                require(state.is_absolute() and state.resolve().is_relative_to(self.root.resolve())
                        and not state.is_symlink() and not (state/'logs').is_symlink()
                        and re.fullmatch(r'call-[0-9a-f]{12}', call) is not None, 'backend journal scope changed')
                pid_path = state/'logs'/(call+'.pid')
                require(pid_path.is_file() and not pid_path.is_symlink(), 'backend PID observation missing')
                pid = json.loads(pid_path.read_bytes())
                require(terminal['child_pid'] == pid['pid'] and terminal['guardian_pid'] == pid['guardian']
                        and terminal.get('started') == pid['started'] and pid.get('call_id') == call
                        and terminal.get('group_termination_confirmed') is True, 'backend launch observation mismatched')
            except (ValueError, TypeError, OSError, KeyError):
                counts['unknown_backend_launches'] += 1;counts['unknown_model_turns'] += 1;continue
            counts['actual_backend_launches'] += 1
            if row['kind'] == 'supervised:backend:reviewer': counts['reviewer_launches'] += 1
            try:
                path = state/'logs'/(call+'.log')
                require(state.is_absolute() and state.resolve().is_relative_to(self.root.resolve())
                        and not state.is_symlink() and not (state/'logs').is_symlink()
                        and str(path) == terminal.get('output')
                        and terminal.get('truncated') is False
                        and file_sha(path) == terminal.get('saved_sha256'), 'backend raw observation missing or mismatched')
                raw = path.read_bytes()
                require(len(raw) == terminal.get('saved_bytes') == terminal.get('output_bytes'), 'backend raw observation incomplete')
                require(type(terminal.get('exit_code')) is int, 'backend terminal exit observation malformed')
                text = raw.decode('utf-8')
                protocol = normalize_exec_events(text)
                if terminal.get('exit_code') == 0 and protocol['valid'] and protocol['model_completed']:
                    counts['successful_model_turns'] += 1
                elif not protocol['valid']:
                    # This precise startup error establishes no model session;
                    # arbitrary missing protocol remains unknown rather than zero.
                    from .runner import observed_startup_infrastructure_failure
                    if not observed_startup_infrastructure_failure(terminal, text): counts['unknown_model_turns'] += 1
            except (ValueError, TypeError, OSError, KeyError): counts['unknown_model_turns'] += 1
        blocked = meta['blocked'] == 'infrastructure_blocked'
        retry = (not meta['stopped'] and not meta['blocked'] and time.time() < meta['deadline']
                 and self.config['budget']['max_calls']-meta['calls'] >= 2)
        counts['recovery'] = {'requires_owner_review': blocked, 'automatic_retry_allowed': bool(retry),
                              'reservation_refund_allowed': False, 'budget_reset_allowed': False,
                              'conditions': ['Restore supported in-process app-server initialization through an authorized runtime environment.',
                                             'Obtain owner review of retained raw failure and drained process journals before any further backend launch.',
                                             'Preserve the original deadline and spent worker/reviewer reservations; resume alone cannot clear the block.'] if blocked else []}
        return counts

    def integrity(self):
        m = dict(self.db.execute('SELECT * FROM meta').fetchone())
        stable_root = Path(m['stable_path']).parent.parent
        require(stable_root.resolve().is_relative_to(self.root) and not stable_root.is_symlink(), 'stable closure outside owner')
        require(canonical(json.loads(m['config'])) == m['config_sha'], 'owner policy hash drift')
        require(file_sha(m['stable_path']) == m['stable_sha']
                and manifest(stable_root) == json.loads(m['stable_manifest']), 'stable runtime integrity drift')
        require(file_sha(self.root/'start.py') == file_sha(stable_root/'entrypoint.py'), 'owner entrypoint integrity drift')
        require(self.trusted_files(self.config) == json.loads(m['trusted']), 'trusted command integrity drift')
        r = self.db.execute('SELECT * FROM releases WHERE id=?', (m['active'],)).fetchone()
        require(r and r['reviewed'] and source_digest(r['path']) == r['source'] and git(r['path'], 'rev-parse', 'HEAD') == r['head']
                and source_digest(Path(r['path'])/'agent_workflow') == r['fingerprint'], 'active release integrity drift')
        return m, dict(r)

    def admit(self):
        m = dict(self.db.execute('SELECT * FROM meta').fetchone())
        if self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='checkpoint_handoffs'").fetchone():
            require(not self.db.execute("SELECT 1 FROM checkpoint_handoffs WHERE phase!='done'").fetchone(), 'checkpoint handoff pending')
        require(not m['stopped'] and not m['blocked'] and time.time() < m['deadline'], 'owner stopped, blocked or expired')
        return m

    def reserve(self, kind, seconds=None, job=None):
        require(isinstance(kind, str) and kind and (job is None or isinstance(job, str)), 'reservation identity required')
        self.integrity()
        seconds = self.config['budget']['call_seconds'] if seconds is None else seconds
        require(type(seconds) in (int, float) and seconds > 0, 'bounded reservation duration required')
        with self.tx():
            m = self.admit(); calls = 2 if kind == 'attempt' else int(kind.startswith('backend:'))
            need = 2 if kind in {'attempt', 'backend:worker'} else calls
            require(self.config['budget']['max_calls'] - m['calls'] >= need, 'independent pair call budget exhausted')
            duration = min(seconds, self.config['budget']['call_seconds'], m['deadline'] - time.time())
            require(duration > 0, 'owner deadline exhausted')
            ident = 'grant-' + uuid.uuid4().hex
            self.db.execute('INSERT INTO reservations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (ident, kind, job, duration, calls, 'reserved', m['active'], m['epoch'], None, None, job,
                 time.time(), encoded({'actual_used': 0}).decode()))
            self.db.execute('UPDATE meta SET calls=calls+?,reserved_seconds=reserved_seconds+?,reserved_bytes=reserved_bytes+?',
                            (calls, duration, self.config['budget']['log_bytes']))
            self.event('reserved', {'id': ident, 'kind': kind, 'calls': calls, 'seconds': duration})
        return ident, duration

    def finish(self, call_id, result):
        require(isinstance(result, dict), 'reservation result required')
        with self.tx():
            row = self.db.execute('SELECT * FROM reservations WHERE id=?', (call_id,)).fetchone()
            require(row is not None, 'unknown reservation')
            old = json.loads(row['result'] or '{}'); value = dict(old, **result)
            value['actual_used'] = max(old.get('actual_used', 0), result.get('actual_used', 0))
            require(value['actual_used'] <= (2 if row['kind'] == 'attempt' else 1), 'backend pair exceeded')
            if row['phase'] == 'finished':
                require(encoded(old) == encoded(value), 'reservation result rebinding'); return
            self.db.execute("UPDATE reservations SET phase='finished',result=? WHERE id=?", (encoded(value).decode(), call_id))
            self.event('finished', {'id': call_id, 'result': value})

    def block(self, reason):
        require(isinstance(reason, str) and reason, 'block reason required')
        with self.tx():
            self.db.execute('UPDATE meta SET blocked=COALESCE(blocked,?)', (reason,)); self.event('blocked', {'reason': reason})

    def set_stop(self, stopped):
        require(type(stopped) is bool, 'explicit boolean stop required')
        with self.tx():
            self.db.execute('UPDATE meta SET stopped=?', (int(stopped),)); self.event('stop' if stopped else 'resume')


class Supervisor(Governor):
    @classmethod
    def checkpoint_handoff(cls, state, config, review_receipt, review_sha):
        """Explicit one-time bootstrap repair; preserves the original grant and history."""
        root = Path(state).resolve()
        require(root.is_dir() and (root/'evolution.sqlite').is_file(), 'original owner checkpoint missing')
        review = _checkpoint_review(root, config, review_receipt, review_sha)
        with (root/'supervisor.lock').open('a') as lock:
            try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError: raise EvolutionError('foreground owner still running')
            db = sqlite3.connect('file:'+str(root/'evolution.sqlite')+'?mode=rw', uri=True, isolation_level=None)
            db.row_factory = sqlite3.Row
            try:
                exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='checkpoint_handoffs'").fetchone()
                prior = db.execute('SELECT * FROM checkpoint_handoffs').fetchone() if exists else None
                require(not prior or prior['phase'] != 'done', 'checkpoint handoff already used')
                m = dict(db.execute('SELECT * FROM meta').fetchone())
                require(canonical(config) == m['config_sha'] and encoded(config).decode() == m['config'], 'original owner configuration changed')
                require(time.time() < m['deadline'], 'original owner deadline exhausted')
                if prior:
                    require(prior['review_sha'] == review_sha and prior['source_sha'] == review['source_sha256'], 'checkpoint intent review changed')
                    proof = json.loads(prior['proof']); ident = prior['id']
                else:
                    # Constructor checks the existing physical closure and trusted command pins.
                    old = cls(root); old.db.close()
                    require(m['stopped'] and m['blocked'] == 'acceptance' and m['failure_class'] == 'acceptance',
                            'only the proven aggregate timeout acceptance block can be recovered')
                    fault = _checkpoint_timeout(db, root, config)
                    require(source_digest(config['repo']) == review['source_sha256']
                            and not git(config['repo'],'status','--porcelain'), 'install the exact independently reviewed clean source before checkpoint')
                    require(not (root/'stable-v2').exists()
                            and not (root/'releases'/('release-'+review['source_sha256'][:16])).exists(), 'checkpoint destination already exists')
                    original = checkpoint(root)
                    ident = 'checkpoint-'+uuid.uuid4().hex
                    proof = {'schema_version':1, 'bootstrap_rescue':True, 'review_sha256':review_sha,
                             'source_sha256':review['source_sha256'], 'original_checkpoint':original,
                             'fault':fault, 'old_meta_sha256':canonical(m), 'previous_active':m['active'],
                             'previous_epoch':m['epoch'], 'previous_stable_path':m['stable_path'],
                             'previous_stable_manifest_sha256':canonical(json.loads(m['stable_manifest'])),
                             'original_launcher_sha256':file_sha(root/'start.py')}
                    db.execute('BEGIN IMMEDIATE')
                    try:
                        db.execute('CREATE TABLE IF NOT EXISTS checkpoint_handoffs(id TEXT PRIMARY KEY,review_sha TEXT,source_sha TEXT,phase TEXT,proof TEXT)')
                        if os.environ.get('AGENT_WORKFLOW_FAULT') == 'checkpoint-before-intent-insert': os._exit(96)
                        db.execute('INSERT INTO checkpoint_handoffs VALUES(?,?,?,?,?)',
                                   (ident, review_sha, review['source_sha256'], 'intent', encoded(proof).decode()))
                        db.execute('COMMIT')
                    except BaseException:
                        db.execute('ROLLBACK');raise
                stable_root = root/'stable-v2'
                if prior and prior['phase'] == 'activated':
                    require(m['active'] == proof['new_release'] and m['epoch'] == proof['previous_epoch']+1
                            and Path(m['stable_path']) == stable_root/'agent_workflow/evolution.py', 'checkpoint activated state conflict')
                else:
                    require(m['active'] == proof['previous_active'] and m['epoch'] == proof['previous_epoch']
                            and m['stopped'] and m['blocked'] == 'acceptance', 'checkpoint original pointer conflict')
                    require(file_sha(proof['previous_stable_path']) == m['stable_sha'], 'original stable module changed')
                    require(manifest(Path(proof['previous_stable_path']).parent.parent) == json.loads(m['stable_manifest']), 'original closure changed')
                    require(source_digest(config['repo']) == review['source_sha256']
                            and not git(config['repo'],'status','--porcelain'), 'install the exact independently reviewed clean source before checkpoint')
                    launcher = ('import sys\nfrom pathlib import Path\nsys.dont_write_bytecode=True\n'
                                'sys.path.insert(0,str(Path(__file__).resolve().parent/"stable-v2"))\n'
                                'from agent_workflow.evolution import main\nraise SystemExit(main())\n')
                    if not stable_root.exists():
                        stage = root/(ident+'-staging')
                        if stage.exists():
                            # Only this intent's never-active staging files may be reconstructed.
                            shutil.rmtree(stage)
                        shutil.copytree(Path(review['source_root'])/'agent_workflow', stage/'agent_workflow',
                                        ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
                        (stage/'entrypoint.py').write_text(launcher)
                        require(source_digest(stage/'agent_workflow') == source_digest(Path(review['source_root'])/'agent_workflow'), 'reviewed closure copy mismatch')
                        stage.rename(stable_root)
                    require(source_digest(stable_root/'agent_workflow') == source_digest(Path(review['source_root'])/'agent_workflow')
                            and (stable_root/'entrypoint.py').read_text() == launcher, 'checkpoint stable-v2 conflict')
                    # Reuse only a release snapshot created by this exact checkpoint intent.
                    release_id = 'release-'+review['source_sha256'][:16]
                    row = db.execute('SELECT * FROM releases WHERE id=?',(release_id,)).fetchone()
                    if row:
                        require(row['source'] == review['source_sha256'] and source_digest(row['path']) == row['source']
                                and json.loads(row['proof']).get('checkpoint_id') == ident, 'checkpoint release conflict')
                    else:
                        destination = root/'releases'/release_id
                        if not destination.exists():
                            staging = root/(ident+'-release-staging')
                            if staging.exists(): shutil.rmtree(staging)
                            git(config['repo'],'clone','--no-local',config['repo'],str(staging))
                            require(source_digest(staging)==review['source_sha256'], 'checkpoint release copy mismatch')
                            staging.rename(destination)
                        require(source_digest(destination)==review['source_sha256']
                                and not git(destination,'status','--porcelain'), 'partial checkpoint release conflict')
                        release_proof={'checkpoint_id':ident,'bootstrap_rescue':True,
                                       'review_sha256':review_sha,'source_manifest':manifest(destination)}
                        db.execute('INSERT INTO releases VALUES(?,?,?,?,?,?,?)',
                            (release_id,str(destination),source_digest(destination/'agent_workflow'),review['source_sha256'],
                             git(destination,'rev-parse','HEAD'),1,encoded(release_proof).decode()))
                    proof['new_release'] = release_id
                    proof['new_stable_manifest_sha256'] = canonical(manifest(stable_root))
                    archive = root/('start.'+ident+'.py')
                    if not archive.exists():
                        require(file_sha(root/'start.py') == proof['original_launcher_sha256'], 'original launcher changed')
                        archive.write_bytes((root/'start.py').read_bytes())
                    require(file_sha(archive) == proof['original_launcher_sha256'], 'checkpoint launcher archive conflict')
                    temporary = root/('start.'+ident+'.pending')
                    temporary.write_text(launcher); os.replace(temporary,root/'start.py')
                    if os.environ.get('AGENT_WORKFLOW_FAULT') == 'checkpoint-before-activation': os._exit(94)
                    db.execute('BEGIN IMMEDIATE')
                    try:
                        changed = db.execute('UPDATE meta SET stable_path=?,stable_sha=?,stable_manifest=?,active=?,epoch=epoch+1,stopped=0,blocked=NULL WHERE active=? AND epoch=? AND stopped=1 AND blocked=\'acceptance\'',
                            (str(stable_root/'agent_workflow/evolution.py'), file_sha(stable_root/'agent_workflow/evolution.py'),
                             encoded(manifest(stable_root)).decode(),release_id,proof['previous_active'],proof['previous_epoch'])).rowcount
                        require(changed == 1,'checkpoint pointer CAS conflict')
                        db.execute("UPDATE checkpoint_handoffs SET phase='activated',proof=? WHERE id=?",(encoded(proof).decode(),ident))
                        db.execute('INSERT INTO events(at,kind,data) VALUES(?,?,?)',(time.time(),'bootstrap_checkpoint_activated',encoded(proof).decode()))
                        db.execute('COMMIT')
                    except BaseException:
                        db.execute('ROLLBACK');raise
                    if os.environ.get('AGENT_WORKFLOW_FAULT') == 'checkpoint-after-activation': os._exit(95)
                # Pending intent fences dispatch until launcher/closure/source have reconciled.
                current = dict(db.execute('SELECT * FROM meta').fetchone())
                require(Path(current['stable_path']) == stable_root/'agent_workflow/evolution.py'
                        and file_sha(current['stable_path']) == current['stable_sha']
                        and manifest(stable_root) == json.loads(current['stable_manifest'])
                        and canonical(manifest(stable_root)) == proof['new_stable_manifest_sha256'],
                        'activated checkpoint closure changed')
                require(file_sha(root/'start.py') == file_sha(stable_root/'entrypoint.py'), 'checkpoint entrypoint mismatch')
                require(source_digest(config['repo']) == review['source_sha256'], 'checkpoint source changed')
                db.execute('BEGIN IMMEDIATE')
                try:
                    db.execute("UPDATE checkpoint_handoffs SET phase='done' WHERE id=?",(ident,))
                    db.execute('INSERT INTO events(at,kind,data) VALUES(?,?,?)',(time.time(),'bootstrap_checkpoint_complete',encoded({'id':ident,'source_sha256':review['source_sha256'],'review_sha256':review_sha,'bootstrap_rescue':True}).decode()))
                    db.execute('COMMIT')
                except BaseException:
                    db.execute('ROLLBACK');raise
                now = dict(db.execute('SELECT * FROM meta').fetchone())
                return {'handoff_id':ident,'bootstrap_rescue':True,'source_sha256':review['source_sha256'],
                        'checkpoint_sha256':proof['original_checkpoint']['checkpoint_sha256'],
                        'started':now['started'],'deadline':now['deadline'],'calls':now['calls'],
                        'reserved_seconds':now['reserved_seconds'],'reserved_bytes':now['reserved_bytes']}
            finally:
                db.close(); fcntl.flock(lock,fcntl.LOCK_UN)

    def reserve_step(self, check_id, child_state, child_config_sha):
        require(check_id in {x['id'] for x in self.config['checks']}, 'unknown owner goal')
        m = self.admit(); cap = self.config['budget']['call_seconds']
        n = len(self.config['tests'])+1; per = min(cap,60); bundle = min(cap,n*per)
        required = (2*cap + n*per + bundle + 4*min(cap,10)
                    + len(self.config['health'])*min(cap,60))
        require(m['deadline']-time.time() >= required, 'wall budget cannot cover worker, independent review and fixed gates')
        return self._grant('attempt', check_id, child_state, child_config_sha)

    def _grant(self, kind, check_id, child_state, child_config_sha):
        require(Path(child_state).is_absolute() and Path(child_state).resolve().is_relative_to(self.root/'children'),
                'child state outside owner root')
        require(child_config_sha == canonical(derive_child_config(self.config)), 'derived child policy SHA mismatch')
        ident, seconds = self.reserve(kind, job=check_id)
        self.db.execute('UPDATE reservations SET child_state=?,child_config_sha=?,check_id=? WHERE id=?',
                        (str(Path(child_state).resolve()), child_config_sha, check_id, ident))
        return ident, seconds

    def _child_paths(self, release):
        directory = self.root/'children'/release['id']; directory.mkdir(parents=True, exist_ok=True)
        config = derive_child_config(self.config); path = directory/'config.json'
        if path.exists(): require(json.loads(path.read_text()) == config, 'child configuration drift')
        else: path.write_bytes(encoded(config))
        return directory/'state', path, canonical(config)

    def _process(self, argv, cwd, log, until, limit):
        """Bounded transport; child per-process guardian receipts remain authoritative."""
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open('xb') as stream:
            p = subprocess.Popen(argv, cwd=cwd, stdout=stream, stderr=subprocess.STDOUT,
                                 env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'), start_new_session=True)
            reason = None
            while p.poll() is None:
                m = dict(self.db.execute('SELECT * FROM meta').fetchone())
                if m['stopped'] or m['blocked']: reason = 'owner_stop'
                elif time.time() >= min(until, m['deadline']): reason = 'owner_deadline'
                elif log.stat().st_size > limit: reason = 'transport_output_limit'
                if reason:
                    try: os.killpg(p.pid, signal.SIGKILL)
                    except ProcessLookupError: pass
                    break
                time.sleep(0.05)
            code = p.wait(timeout=5)
        with log.open('rb') as stream: raw = stream.read(limit)
        if log.stat().st_size > limit:
            with log.open('r+b') as f: f.truncate(limit)
        return {'argv': argv, 'exit_code': code, 'reason': reason, 'log': str(log),
                'log_sha256': file_sha(log), 'process_terminal': True}, raw

    def _child(self, action, release, state, config_path, grant):
        m = self.admit(); argv = [sys.executable, '-m', 'agent_workflow.runner', '--state', str(state), action,
            '--config', str(config_path), '--governor-state', str(self.root), '--grant-id', grant,
            '--release', release['id'], '--active-fingerprint', release['fingerprint']]
        # Exact active source provides discovery and execution; cwd determines package resolution.
        log = self.root/'transport'/(grant+('-recover-'+uuid.uuid4().hex if action=='recover' else '')+'.log')
        result, raw = self._process(argv, release['path'], log, m['deadline'], max(262144, self.config['budget']['log_bytes']*8))
        if result['reason'] == 'owner_stop' or self.status()['meta']['stopped']:
            # Keep a claimed journal pending for bounded, no-dispatch recovery after explicit resume.
            self.event('child_paused', {'grant': grant, 'release': release['id'], 'result': result})
            return {'schema_version': 1, 'phase': 'idle', 'paused': True,
                    'active_fingerprint': release['fingerprint'], 'job_id': None, 'check_id': None,
                    'source_fingerprint': source_digest(self.config['repo']), 'receipt': None,
                    'failure_class': None, 'calls': []}
        if result['exit_code'] != 0 or result['reason']:
            self.event('child_exit', {'grant': grant, 'action': action, 'result': result})
            row = self.db.execute('SELECT phase FROM reservations WHERE id=?', (grant,)).fetchone()
            if action != 'recover' and row and row['phase'] == 'claimed':
                # Recovery reads terminal guardian/Core/CAS journals only; the child cannot launch again.
                return self._child('recover', release, state, config_path, grant)
            self.finish(grant, result)
            raise EvolutionError('active child failed; terminal recovery required')
        try: body = json.loads(raw)
        except ValueError as error: raise EvolutionError('active child protocol malformed') from error
        if body.get('ok') is True: body = body.get('result')
        require(isinstance(body, dict) and body.get('schema_version') == 1
                and body.get('phase') in {'idle', 'complete', 'failed', 'blocked'}
                and body.get('active_fingerprint') == release['fingerprint'], 'active child identity/protocol mismatch')
        if body['phase'] == 'complete':
            bound = self.db.execute('SELECT result FROM reservations WHERE id=?', (grant,)).fetchone()
            grant_facts = json.loads(bound['result'] or '{}')
            require(grant_facts.get('job_id') == body['job_id'] and isinstance(body.get('receipt'), dict)
                    and body['receipt'].get('base_head') == grant_facts.get('base_head')
                    and body['receipt'].get('baseline') == grant_facts.get('source'),
                    'completion is not the exact job/source bound to this attempt')
        self.finish(grant, dict(result, child_result=body))
        if body['phase'] == 'complete':
            record = self.db.execute('SELECT active_epoch FROM reservations WHERE id=?', (grant,)).fetchone()
            self.db.execute('INSERT OR IGNORE INTO completions VALUES(?,?,?,?,?,?)',
                (body['job_id'], release['id'], record['active_epoch'], str(state), encoded(body).decode(), 'intent'))
        self.event('child_result', {'action': action, 'grant': grant, 'release': release['id'], 'result': body})
        return body

    def _drained(self, state):
        path = Path(state)/'runner.sqlite'
        require(path.is_file(), 'child journal missing')
        db = sqlite3.connect('file:'+str(path)+'?mode=ro', uri=True)
        try:
            require(not db.execute("SELECT 1 FROM calls WHERE phase!='finished'").fetchone(), 'unfinished process guardian')
            require(not db.execute("SELECT 1 FROM integration WHERE phase!='done'").fetchone(), 'unfinished integration')
        finally: db.close()
        for core in Path(state).rglob('tasks.sqlite'):
            db = sqlite3.connect('file:'+str(core)+'?mode=ro', uri=True)
            try:
                require(not db.execute("SELECT 1 FROM runs WHERE state IN ('running','changed','verifying','verified','ready_review')").fetchone(),
                        'active Core run prevents release drain')
            finally: db.close()
        self.event('drained', {'child_state': str(state)})

    def _proof(self, state, result, release, epoch):
        # Re-observe canonical raw facts through stable, SHA-bound validation code.
        from .receipt import build_receipt, normalize_exec_events, diff_manifest
        path = Path(state)/'receipts'/(result['job_id']+'.json')
        require(path.is_file() and not path.is_symlink(), 'canonical completion receipt missing')
        receipt = json.loads(path.read_text())
        rebuilt = build_receipt({k: v for k, v in receipt.items() if k not in {'schema_version', 'complete', 'receipt_id'}})
        require(rebuilt == receipt and receipt.get('runtime') == release['fingerprint'], 'receipt active controller binding mismatch')
        repo = self.config['repo']
        require(git(repo, 'rev-parse', 'HEAD') == receipt['head'] and source_digest(repo) == receipt['candidate'],
                'receipt current source/CAS mismatch')
        require(diff_manifest(repo, receipt['base_head']) == receipt['manifest'], 'candidate full diff manifest mismatch')
        for entry in receipt['manifest']['entries']:
            for changed_path in (entry['path'], entry.get('old_path')):
                if changed_path is not None:
                    safe_path(changed_path)
                    require(not protected(changed_path) and any(fnmatch.fnmatchcase(changed_path, glob) for glob in self.config['allowlist']),
                            'candidate path outside fixed permissions')
        db = sqlite3.connect('file:'+str(Path(state)/'runner.sqlite')+'?mode=ro', uri=True); db.row_factory = sqlite3.Row
        try:
            job = db.execute('SELECT * FROM jobs WHERE id=?', (result['job_id'],)).fetchone()
            require(job and job['phase'] == 'complete' and job['candidate'] == receipt['candidate']
                    and job['candidate_commit'] == receipt['head'], 'canonical job completion absent')
            intent = db.execute('SELECT * FROM integration WHERE job_id=?', (job['id'],)).fetchone()
            require(intent and intent['phase'] == 'done' and intent['expected'] == receipt['base_head']
                    and intent['candidate'] == receipt['head'], 'canonical CAS intent absent')
            slot = Path(job['slot']).resolve(); require(slot.is_relative_to(Path(state).resolve()), 'job artifact outside child state')
            core = sqlite3.connect('file:'+str(slot/'core-state/tasks.sqlite')+'?mode=ro', uri=True); core.row_factory = sqlite3.Row
            try:
                run = core.execute('SELECT * FROM runs WHERE id=?', (receipt['run_id'],)).fetchone()
                require(run and run['state'] == 'accepted' and run['task_id'] == receipt['task_id']
                        and json.loads(run['evidence']) == receipt['core_state']['evidence'], 'canonical accepted Core evidence mismatch')
                failures = [json.loads(x[0]) for x in core.execute("SELECT data FROM events WHERE run_id=? AND kind='verification_failed'", (receipt['run_id'],))]
                require(any(x.get('baseline') == receipt['baseline'] == x.get('candidate') for x in failures), 'fresh exact baseline failure absent')
            finally: core.close()
            evidence = []
            prepared = [x['at'] for x in db.execute("SELECT at,data FROM events WHERE job_id=? AND kind='attempt_prepared'", (job['id'],))
                        if json.loads(x['data']).get('slot') == str(slot)]
            require(prepared, 'canonical attempt preparation absent')
            since = max(prepared)
            for row in db.execute("SELECT * FROM calls WHERE job_id=? AND phase='finished'", (job['id'],)):
                observed = json.loads(row['result'] or '{}'); output = Path(observed.get('output', '')).resolve()
                if observed.get('started', 0) < since: continue
                require(output.parent == (Path(state)/'logs').resolve() and output.is_file(), 'protected process log absent')
                pid = json.loads((Path(state)/'logs'/(row['id']+'.pid')).read_text())
                require(observed.get('group_termination_confirmed') is True and observed.get('call_id') == row['id'] == pid.get('call_id')
                        and observed.get('child_pid') == pid.get('pid') and observed.get('guardian_pid') == pid.get('guardian')
                        and observed.get('started') == pid.get('started'), 'raw guardian terminal identity unconfirmed')
                sha = file_sha(output)
                require(sha == (observed.get('saved_sha256') or observed.get('output_sha256')), 'raw process log SHA mismatch')
                if row['kind'] in {'backend:worker', 'backend:reviewer'}:
                    protocol = normalize_exec_events(output.read_text(errors='replace'))
                    require(observed['exit_code'] == 0 and protocol['valid'] and protocol['model_completed'], 'fresh backend observation incomplete')
                evidence.append({'kind': row['kind'], 'result': observed, 'raw_log_sha256': sha})
            require({x['kind'] for x in evidence} >= {'backend:worker', 'backend:reviewer', 'machine-test'}, 'worker/reviewer/machine observations missing')
            verdict = slot/'verdict.json'; review = json.loads(verdict.read_text())
            require(file_sha(verdict) == receipt['reviewer']['verdict_sha256'] and review.get('approved') is True
                    and review.get('candidate') == receipt['candidate'], 'fresh independent review mismatch')
            check = next(x for x in self.config['checks'] if x['id'] == job['check_id'])
            expected_argv = [[arg.replace('{repo}', str(slot/'candidate')) for arg in argv]
                             for argv in self.config['tests']+[check['argv']]]
            require([x['argv'] for x in receipt['tests']] == expected_argv, 'owner machine commands mismatch')
        finally: db.close()
        m, _ = self.integrity()
        proof = {'schema_version': 1, 'active_release': release['id'], 'active_epoch': epoch,
                 'owner_config_sha256': m['config_sha'], 'stable_manifest_sha256': canonical(json.loads(m['stable_manifest'])),
                 'trusted_manifest_sha256': canonical(json.loads(m['trusted'])), 'receipt_sha256': file_sha(path),
                 'receipt': receipt, 'process_evidence': evidence, 'source_manifest': manifest(repo),
                 'evidence_kind': self.config['execution_mode'], 'external_provider_calls': 0 if self.config['execution_mode']=='native_fixture' else None}
        proof['proof_sha256'] = canonical(proof)
        self.event('independent_proof', proof)
        return proof

    def _health(self, source):
        before = source_digest(source); results = []
        for template in self.config['health']:
            ident, seconds = self.reserve('health', seconds=min(60, self.config['budget']['call_seconds']))
            argv = [x.replace('{repo}', str(source)) for x in template]
            result, _ = self._process(argv, source, self.root/'health'/(ident+'.log'), time.time()+seconds,
                                      self.config['budget']['log_bytes'])
            self.finish(ident, result); results.append(result)
            require(result['exit_code'] == 0 and not result['reason'], 'release health rejected')
        require(source_digest(source) == before, 'health changed release source')
        self.event('health_passed', {'source': str(source), 'fingerprint': before, 'results': results})
        return results

    def _failure(self, failure_class, data=None):
        failure_class = failure_class or 'attempt'
        with self.tx():
            row = self.db.execute('SELECT * FROM meta').fetchone()
            count = row['failure_count'] + 1 if row['failure_class'] == failure_class else 1
            self.db.execute('UPDATE meta SET failure_class=?,failure_count=?', (failure_class, count))
            self.event('failure', {'class': failure_class, 'count': count, 'evidence': data or {}})
            if failure_class in {'integrity', 'permissions', 'acceptance', 'auth', 'quota', 'recovery',
                                 'recovery_blocked', 'recovery_requires_owner_review', 'budget_blocked', 'infrastructure_blocked'} or count >= 2:
                self.db.execute('UPDATE meta SET blocked=COALESCE(blocked,?),stopped=CASE WHEN ? IN (\'integrity\',\'permissions\',\'acceptance\',\'infrastructure_blocked\') THEN 1 ELSE stopped END',
                                (failure_class, failure_class))
        return {'schema_version': 1, 'phase': 'blocked' if self.status()['meta']['blocked'] else 'failed', 'failure_class': failure_class}

    def _activate(self, release, candidate, proof, child_state):
        self._drained(child_state); m = self.admit()
        require(m['active'] == release['id'] and m['epoch'] == proof['active_epoch'], 'activation pointer conflict')
        require(source_digest(self.config['repo']) == candidate['source']
                and git(self.config['repo'], 'rev-parse', 'HEAD') == candidate['head'], 'activation source changed')
        ident = 'activation-'+uuid.uuid4().hex
        with self.tx():
            self.db.execute('INSERT INTO activation VALUES(?,?,?,?,?,?,?)',
                (ident, release['id'], m['epoch'], candidate['id'], candidate['head'], candidate['source'], 'intent'))
            self.event('activation_intent', {'id': ident, 'expected': release['id'], 'epoch': m['epoch'], 'candidate': candidate['id']})
        if os.environ.get('AGENT_WORKFLOW_FAULT') == 'evolution-before-activation': os._exit(93)
        self._reconcile_activation()

    def _reconcile_activation(self):
        for intent in self.db.execute("SELECT * FROM activation WHERE phase!='done'").fetchall():
            self.admit(); candidate = self.db.execute('SELECT * FROM releases WHERE id=?', (intent['candidate'],)).fetchone()
            require(candidate and candidate['reviewed'] and source_digest(candidate['path']) == intent['source']
                    and git(self.config['repo'], 'rev-parse', 'HEAD') == intent['head']
                    and source_digest(self.config['repo']) == intent['source'], 'activation recovery source conflict')
            with self.tx():
                m = self.admit()
                if m['active'] == intent['expected'] and m['epoch'] == intent['epoch']:
                    cursor = self.db.execute('UPDATE meta SET active=?,epoch=epoch+1 WHERE active=? AND epoch=? AND stopped=0 AND blocked IS NULL',
                        (intent['candidate'], intent['expected'], intent['epoch']))
                    require(cursor.rowcount == 1, 'activation CAS conflict')
                    self.event('activated', {'id': intent['id'], 'release': intent['candidate'], 'previous': intent['expected'], 'epoch': intent['epoch']+1})
                else: require(m['active'] == intent['candidate'] and m['epoch'] == intent['epoch']+1, 'activation pointer conflict')
                self.db.execute("UPDATE activation SET phase='done' WHERE id=?", (intent['id'],))

    def _recover(self):
        self._reconcile_activation()
        # Crash between receipt observation and intent insertion remains recoverable without calls.
        for grant in self.db.execute("SELECT * FROM reservations WHERE kind='attempt' AND phase='finished'").fetchall():
            result = json.loads(grant['result'] or '{}').get('child_result')
            if result and result.get('phase') == 'complete':
                self.db.execute('INSERT OR IGNORE INTO completions VALUES(?,?,?,?,?,?)',
                    (result['job_id'], grant['release_id'], grant['active_epoch'], grant['child_state'], encoded(result).decode(), 'intent'))
        for completion in self.db.execute("SELECT * FROM completions WHERE phase!='done'").fetchall():
            result = json.loads(completion['result']); state = Path(completion['child_state'])
            release = dict(self.db.execute('SELECT * FROM releases WHERE id=?', (completion['release_id'],)).fetchone())
            proof = self._proof(state, result, release, completion['epoch'])
            self._complete(result, release, proof, state)
        unresolved = self.db.execute("SELECT * FROM reservations WHERE kind IN ('probe','attempt') AND phase!='finished' AND child_state IS NOT NULL ORDER BY rowid").fetchall()
        for grant in unresolved:
            # Never redispatch a charged or claimed attempt. Existing child journals may settle CAS.
            if grant['phase'] == 'reserved':
                self.finish(grant['id'], {'recovered': True, 'dispatched': False}); continue
            release = dict(self.db.execute('SELECT * FROM releases WHERE id=?', (grant['release_id'],)).fetchone())
            state = Path(grant['child_state']); config_path = state.parent/'config.json'
            result = self._child('recover', release, state, config_path, grant['id'])
            require(result['phase'] in {'idle', 'complete'}, 'child process recovery blocked')
            if result['phase'] == 'complete':
                proof = self._proof(state, result, release, grant['active_epoch'])
                self._complete(result, release, proof, state)

    def _complete(self, result, release, proof, state):
        self._drained(state)
        if self.config['project']['kind'] == 'target':
            self._health(self.config['repo']); self.event('target_complete', {'job': result['job_id'], 'proof_sha256': proof['proof_sha256']})
        else:
            expected_id = 'release-' + source_digest(self.config['repo'])[:16]
            existing = self.db.execute('SELECT * FROM releases WHERE id=?', (expected_id,)).fetchone()
            candidate = dict(existing) if existing else self._snapshot(self.config['repo'], proof=proof)
            current = self.status()['meta']
            if current['active'] == candidate['id'] and current['epoch'] == proof['active_epoch']+1:
                self.db.execute("UPDATE completions SET phase='done' WHERE job=?", (result['job_id'],))
                return dict(result, deployed=True, recovered=True)
            try: self._health(candidate['path'])
            except EvolutionError as error:
                self.event('release_rejected', {'release': candidate['id'], 'reason': str(error), 'previous': release['id']})
                self.db.execute("UPDATE completions SET phase='done' WHERE job=?", (result['job_id'],))
                return self._failure('health', {'reason': str(error), 'release': candidate['id']})
            self._activate(release, candidate, proof, state)
        self.db.execute('UPDATE meta SET failure_class=NULL,failure_count=0,idle_count=0')
        self.db.execute("UPDATE completions SET phase='done' WHERE job=?", (result['job_id'],))
        return dict(result, deployed=self.config['project']['kind']=='controller')

    def maintenance_tick(self):
        """Queue opt-in UI investigations under live owner admission; never dispatch a backend."""
        with self.locked():
            m = dict(self.db.execute('SELECT * FROM meta').fetchone())
            descriptor = self.config['project'].get('uiux')
            if not descriptor:
                return {'phase': 'not_applicable', 'production_visual_verified': False}
            def gate():
                latest = dict(self.db.execute('SELECT * FROM meta').fetchone())
                return (not latest['stopped'] and not latest['blocked'] and time.time() < latest['deadline']
                        and self.config['budget']['max_calls']-latest['calls'] >= 2)
            if not gate(): return {'phase': 'paused', 'production_visual_verified': False}
            scheduler = None
            try:
                from .uiux import read_regular, strict_json, validate_policy
                from .uiux_maintenance import UIUXMaintenance
                raw = read_regular(Path(descriptor['path']), 1048576)
                require(hashlib.sha256(raw).hexdigest() == descriptor['sha256'], 'owner UIUX policy changed')
                plan = validate_policy(strict_json(raw))
                require(plan['project']['id'] == self.config['project']['id'], 'owner UIUX project changed')
                if not plan['project']['has_ui']:
                    return {'phase': 'not_applicable', 'production_visual_verified': False}
                state = self.root/'uiux-core'
                def sink(proposal):
                    require(gate(), 'owner UIUX admission closed')
                    from .core import Engine
                    b = self.config['budget']
                    policy = {'repo': self.config['repo'], 'tests': [[arg.replace('{repo}', self.config['repo']) for arg in argv] for argv in self.config['tests']],
                              'sources': ['local'], 'budget': {'lease_seconds': b['wall_seconds'], 'run_seconds': b['wall_seconds'],
                                  'test_seconds': min(b['call_seconds'], b['wall_seconds']), 'max_attempts': b.get('max_attempts', 1)},
                              'acceptance': {'require_tests': True, 'require_unchanged_candidate': True},
                              'permissions': {'execute_tests': True, 'network': False, 'publish': False}, 'uiux_policy': descriptor}
                    engine = Engine(state, policy)
                    try:
                        require(gate(), 'owner UIUX admission closed')
                        return engine._uiux_task(proposal)
                    finally: engine.db.close()
                scheduler = UIUXMaintenance(state, plan, self.config['repo'], task_sink=sink, gate=gate)
                return scheduler.tick(stopped=bool(m['stopped']))
            except Exception as error:
                # UI investigation failures are isolated from controller admission and release health.
                return {'phase': 'blocked', 'failure_class': 'integrity', 'error': type(error).__name__, 'production_visual_verified': False}
            finally:
                if scheduler is not None and scheduler.db is not None: scheduler.db.close()

    def cycle(self):
        with self.locked():
            try:
                if self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='checkpoint_handoffs'").fetchone():
                    if self.db.execute("SELECT 1 FROM checkpoint_handoffs WHERE phase!='done'").fetchone():
                        return {'schema_version':1,'phase':'idle','paused':True,'failure_class':None,
                                'reason':'checkpoint handoff pending'}
                m, release = self.integrity()
                if m['stopped'] or m['blocked'] or time.time() >= m['deadline']:
                    return {'schema_version': 1, 'phase': 'blocked' if m['blocked'] else 'idle', 'paused': True, 'failure_class': m['blocked']}
                self._recover(); m, release = self.integrity()
                uiux_result = self.maintenance_tick()
                if uiux_result['phase'] not in {'not_applicable', 'not_due'}:
                    keys = {'phase', 'run_id', 'next_due', 'failure_count', 'policy_sha256', 'production_visual_verified', 'error', 'failure_class'}
                    self.event('uiux_maintenance_checked', {'result': {key: uiux_result[key] for key in keys if key in uiux_result}})
                maintenance = self.config['project'].get('maintenance')
                if maintenance:
                    from .maintenance import MaintenanceScheduler
                    def maintenance_gate():
                        latest = self.status()['meta']
                        return (not latest['stopped'] and not latest['blocked'] and time.time()<latest['deadline']
                                and self.config['budget']['max_calls']-latest['calls'] >= 2)
                    scheduler = MaintenanceScheduler(self.root/'maintenance', json.loads(Path(maintenance['config']).read_text()),
                                                     gate=maintenance_gate,
                                                     deadline=lambda: self.status()['meta']['deadline'])
                    try:
                        findings = scheduler.tick(stopped=False,
                            budget_available=self.config['budget']['max_calls']-m['calls'] >= 2)
                    finally: scheduler.db.close()
                    # Notices are data only. The active release still must reproduce an existing fixed owner check.
                    self.event('maintenance_checked', {'result': findings, 'fixed_check_ids': [x['id'] for x in self.config['checks']]})
                self.event('cycle', {'release': release['id'], 'fingerprint': release['fingerprint'], 'epoch': m['epoch'], 'project': self.config['project']})
                state, config_path, sha = self._child_paths(release)
                grant, _ = self._grant('probe', None, state, sha)
                result = self._child('probe', release, state, config_path, grant)
                if result['phase'] in {'failed', 'blocked'}: return self._failure(result['failure_class'], result)
                if not result.get('job_id'):
                    self.db.execute('UPDATE meta SET idle_count=idle_count+1')
                    return result
                grant, _ = self.reserve_step(result['check_id'], state, sha)
                result = self._child('step', release, state, config_path, grant)
                if result['phase'] != 'complete': return self._failure(result['failure_class'], result)
                proof = self._proof(state, result, release, m['epoch'])
                return self._complete(result, release, proof, state)
            except EvolutionError as error:
                # Integrity is terminal; budget admission denial is a bounded pause.
                message = str(error)
                if 'budget' in message or 'deadline' in message or 'stopped, blocked' in message:
                    return {'schema_version': 1, 'phase': 'blocked', 'failure_class': 'budget', 'reason': message}
                cls = 'recovery' if 'terminal recovery' in message or 'recovery blocked' in message else 'integrity'
                self._failure(cls, {'reason': message}); raise

    step = cycle

    def loop(self, max_cycles=None):
        require(max_cycles is None or type(max_cycles) is int and max_cycles > 0, 'positive cycle bound required')
        count = 0
        with self.locked():
            while True:
                m = self.status()['meta']
                if m['stopped'] or m['blocked'] or time.time() >= m['deadline']: break
                result = self.cycle(); count += 1
                if result.get('paused'): break
                if max_cycles is not None and count >= max_cycles: break
                if result.get('phase') == 'idle':
                    idle = self.status()['meta']['idle_count']; cfg = self.config['idle']
                    if cfg == {'initial_seconds': 30, 'max_seconds': 900}:
                        delay = (30, 60, 120, 900)[min(max(idle-1, 0), 3)]
                    else: delay = min(cfg['max_seconds'], cfg['initial_seconds'] * 2**min(max(idle-1, 0), 20))
                    until = min(time.time()+delay, m['deadline'])
                    while time.time() < until:
                        latest = self.status()['meta']
                        if latest['stopped'] or latest['blocked']: break
                        time.sleep(min(0.1, max(0, until-time.time())))
        return self.status()


def checkpoint(state):
    """Read-only hashes of owner facts; private journal payloads never leave this API."""
    root = Path(state).resolve(); path=root/'evolution.sqlite'
    require(path.is_file() and not path.is_symlink(),'initialized checkpoint missing')
    db=sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
    try:
        db.execute('BEGIN');meta=dict(db.execute('SELECT * FROM meta').fetchone());tables={}
        for name in ('releases','reservations','events','activation','completions'):
            rows=[dict(x) for x in db.execute('SELECT * FROM '+name+' ORDER BY 1')]
            tables[name]={'rows':len(rows),'sha256':canonical(rows)}
        value={'schema_version':1,'meta_sha256':canonical(meta),'tables':tables,
               'started':meta['started'],'deadline':meta['deadline'],'calls':meta['calls'],
               'reserved_seconds':meta['reserved_seconds'],'reserved_bytes':meta['reserved_bytes'],
               'config_sha256':meta['config_sha'],'stable_manifest_sha256':canonical(json.loads(meta['stable_manifest']))}
        value['checkpoint_sha256']=canonical(value);return value
    finally:db.close()


def _checkpoint_review(root, config, receipt_path, expected_sha):
    require(isinstance(config,dict),'original owner config required')
    path=Path(receipt_path)
    require(path.is_absolute() and not path.resolve().is_relative_to(root) and file_sha(path)==expected_sha,
            'independent checkpoint review artifact SHA missing or changed')
    review=json.loads(path.read_text())
    fields={'schema_version','approved','source_root','source_sha256','core_state','run_id','reviewer'}
    require(isinstance(review,dict) and set(review)==fields and review['schema_version']==1
            and review['approved'] is True and isinstance(review['reviewer'],str) and review['reviewer'],
            'exact independent checkpoint review required')
    source=Path(review['source_root']);core=Path(review['core_state'])
    require(source.is_absolute() and core.is_absolute() and not path.resolve().is_relative_to(source.resolve())
            and not core.resolve().is_relative_to(source.resolve())
            and source_digest(source)==review['source_sha256']
            and source_digest(Path(__file__).parent.parent)==review['source_sha256'], 'reviewed checkpoint source changed')
    dbpath=core/'tasks.sqlite';require(dbpath.is_file() and not dbpath.is_symlink(),'actual accepted C3 state missing')
    db=sqlite3.connect('file:'+str(dbpath)+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
    try:
        run=db.execute('SELECT * FROM runs WHERE id=?',(review['run_id'],)).fetchone()
        require(run and run['state']=='accepted' and run['candidate']==review['source_sha256'], 'checkpoint source not actually C3 accepted')
        evidence=json.loads(run['evidence'] or '{}');policy=json.loads(db.execute('SELECT body FROM policy WHERE id=1').fetchone()[0])
        require(evidence.get('candidate')==run['candidate'] and evidence.get('baseline')==run['baseline']
                and evidence.get('workflow')==run['workflow'] and evidence.get('source_unchanged') is True
                and evidence.get('policy')==policy and Path(policy['repo']).resolve()==source.resolve(), 'accepted C3 evidence binding changed')
        require(policy['acceptance']=={'require_tests':True,'require_unchanged_candidate':True}
                and policy['permissions'].get('network') is False and policy['permissions'].get('publish') is False,
                'checkpoint review has broader authority')
        results=evidence.get('results');require(isinstance(results,list) and results
                and [x.get('argv') for x in results]==policy['tests']
                and all(x.get('exit_code')==0 and not x.get('execution_error') for x in results), 'checkpoint C3 machine evidence incomplete')
        review['core_evidence_sha256']=canonical(evidence)
    finally:db.close()
    return review


def _checkpoint_timeout(db, root, config):
    grant=db.execute("SELECT * FROM reservations WHERE kind='attempt' AND phase='finished' ORDER BY rowid DESC LIMIT 1").fetchone()
    require(grant is not None,'failed original attempt proof missing')
    facts=json.loads(grant['result'] or '{}');result=facts.get('child_result',{})
    require(facts.get('actual_used')==1 and facts.get('backend_kinds')==['backend:worker']
            and result.get('phase')=='failed' and result.get('failure_class')=='acceptance'
            and result.get('receipt') is None and result.get('job_id')==facts.get('job_id'), 'original incident is not the single-worker aggregate timeout')
    state=Path(grant['child_state']).resolve();require(state.is_relative_to(root/'children'),'original child state outside owner')
    sub=sqlite3.connect('file:'+str(state/'runner.sqlite')+'?mode=ro',uri=True);sub.row_factory=sqlite3.Row
    try:
        job=sub.execute('SELECT * FROM jobs WHERE id=?',(facts['job_id'],)).fetchone()
        require(job and job['check_id']==grant['check_id'] and job['baseline']==facts['source']
                and job['base_head']==facts['base_head'] and job['run_id'] and not job['candidate_commit'], 'original failed job binding changed')
        require(not sub.execute("SELECT 1 FROM calls WHERE phase!='finished'").fetchone()
                and not sub.execute('SELECT 1 FROM integration').fetchone()
                and not (state/'receipts').exists(), 'original incident has unfinished process or acceptance/CAS')
        slot=Path(job['slot']).resolve();require(slot.is_relative_to(state),'original slot outside child')
        rows=sub.execute('SELECT * FROM calls WHERE job_id=? ORDER BY rowid',(job['id'],)).fetchall()
        verify=[x for x in rows if x['kind']=='workflow:verify'];require(len(verify)==1,'aggregate verify incident ambiguous')
        observed=json.loads(verify[0]['result'] or '{}')
        require(observed.get('exit_code')==124 and observed.get('reason')=='timeout'
                and observed.get('group_termination_confirmed') is True,'aggregate timeout terminal evidence missing')
        def terminal(row):
            value=json.loads(row['result'] or '{}');log=Path(value.get('output','')).resolve()
            require(log.parent==(state/'logs').resolve() and file_sha(log)==value.get('saved_sha256'), 'original raw log changed')
            pid=json.loads((state/'logs'/(row['id']+'.pid')).read_text())
            spec=json.loads((state/'logs'/(row['id']+'.spec.json')).read_text())
            require(value.get('group_termination_confirmed') is True and value.get('call_id')==pid.get('call_id')==row['id']
                    and value.get('child_pid')==pid.get('pid') and value.get('guardian_pid')==pid.get('guardian')
                    and value.get('started')==pid.get('started') and spec.get('call_id')==row['id']
                    and spec.get('argv')==value.get('argv') and 0<spec.get('seconds',0)<=config['budget']['call_seconds'],
                    'original process terminal identity or command changed')
            return value
        terminal(verify[0]);require(observed['argv'][1:3]==['-m','agent_workflow'] and 'verify' in observed['argv'], 'original timeout was not Core verification')
        require(not any(x['kind']=='backend:reviewer' for x in rows),'old reviewer acceptance may not be reused')
        check=next((x for x in config['checks'] if x['id']==job['check_id']),None);require(check,'original goal registry mismatch')
        expected=[[x.replace('{repo}',str(slot/'candidate')) for x in argv] for argv in config['tests']+[check['argv']]]
        machine=[x for x in rows if x['kind']=='machine-test' and json.loads(x['result']).get('started',0)<observed['started']]
        require(len(machine)==len(expected),'original machine denominator incomplete')
        for row,argv in zip(machine,expected):
            value=terminal(row);require(value['argv']==argv and value['exit_code']==0 and value.get('reason') is None,'original fixed machine command failed or changed')
        core=sqlite3.connect('file:'+str(slot/'core-state/tasks.sqlite')+'?mode=ro',uri=True);core.row_factory=sqlite3.Row
        try:
            run=core.execute('SELECT * FROM runs WHERE id=?',(job['run_id'],)).fetchone()
            policy=json.loads(core.execute('SELECT body FROM policy WHERE id=1').fetchone()[0])
            require(run and run['state']=='verifying' and run['candidate']==job['candidate'] and policy['tests']==expected,'historical Core timeout state changed')
            started=[json.loads(x[0]) for x in core.execute("SELECT data FROM events WHERE run_id=? AND kind='verification_started'",(job['run_id'],))]
            require(any(x.get('candidate')==job['candidate'] for x in started),'historical verification start missing')
        finally:core.close()
        archive=slot/'failed-source'
        require(archive.is_dir() and source_digest(archive)==job['candidate'],'failed exact source archive missing')
        return {'classification':'aggregate_core_timeout','grant_id':grant['id'],'job_id':job['id'],
                'call_id':verify[0]['id'],'raw_log_sha256':observed['saved_sha256'],
                'machine_count':len(machine),'fault_evidence_sha256':canonical({'job':dict(job),'calls':[dict(x) for x in rows]}),
                'reviewer_used':False,'accepted':False}
    finally:sub.close()


def _public_output(value):
    """Public projection only; canonical private facts remain unchanged."""
    from .redaction import public
    return public(value)


def main():
    parser = argparse.ArgumentParser(description='Stable foreground local release supervisor')
    parser.add_argument('--state', required=True); parser.add_argument('--config')
    parser.add_argument('--max-cycles', type=int)
    parser.add_argument('--review-receipt'); parser.add_argument('--review-sha')
    parser.add_argument('action', choices=['init', 'start', 'resume', 'stop', 'status', 'checkpoint-handoff'])
    args = parser.parse_args(); root = Path(args.state).resolve()
    try:
        if args.action == 'checkpoint-handoff':
            require(args.config and args.review_receipt and args.review_sha, 'checkpoint config and independent review required')
            result = Supervisor.checkpoint_handoff(root, json.loads(Path(args.config).read_text()), args.review_receipt, args.review_sha)
            print(json.dumps(_public_output({'ok':True,'result':result}),sort_keys=True));return 0
        # Inspect immutable owner facts before re-entering; do not overwrite launcher or runtime.
        supervisor = Supervisor(root, json.loads(Path(args.config).read_text()) if args.config else None)
        stable = Path(supervisor.db.execute('SELECT stable_path FROM meta').fetchone()[0])
        if Path(__file__).resolve() != stable and args.action != 'init':
            supervisor.db.close()
            os.execv(sys.executable, [sys.executable, str(root/'start.py'), *sys.argv[1:]])
        if args.action == 'stop': supervisor.set_stop(True); result = supervisor.status()
        elif args.action == 'resume': supervisor.set_stop(False); result = supervisor.loop(args.max_cycles)
        elif args.action == 'start': result = supervisor.loop(args.max_cycles)
        else: result = supervisor.status()
        print(json.dumps(_public_output({'ok': True, 'result': result}), sort_keys=True))
    except (EvolutionError, OSError, ValueError) as error:
        print(json.dumps(_public_output({'ok': False, 'error': str(error)}))); return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
