import base64
import contextlib
import hashlib
import json
import os
from pathlib import Path
import secrets
import signal
import sqlite3
import subprocess
import threading
import time
import uuid

from . import core_process


class WorkflowError(ValueError):
    pass


OUTPUT_CAP = 65536


class _OutputDrain:
    """Drain one pipe to EOF without retaining more than its raw prefix."""

    def __init__(self, stream):
        self.stream = stream
        self.prefix = bytearray()
        self.total = 0
        self.error = None
        self.thread = threading.Thread(target=self.drain)

    def drain(self):
        try:
            while chunk := self.stream.read(8192):
                self.total += len(chunk)
                remaining = OUTPUT_CAP - len(self.prefix)
                if remaining:
                    self.prefix.extend(chunk[:remaining])
        except Exception as error:
            self.error = type(error).__name__ + ': ' + str(error)


def digest(root):
    """Content identity for source, including untracked files, excluding runtime."""
    h = hashlib.sha256()
    root = Path(root).resolve()
    for p in sorted(root.rglob('*')):
        rel = p.relative_to(root)
        if any(x in {'.git', '.workflow', '__pycache__'} for x in rel.parts) or p.suffix == '.pyc':
            continue
        if p.is_symlink():
            raise WorkflowError('source symlinks are unsupported')
        if p.is_file():
            h.update(str(rel).encode() + b'\0' + p.read_bytes() + b'\0')
    return h.hexdigest()


def validate_config(c):
    required = {'repo', 'tests', 'sources', 'budget', 'acceptance', 'permissions'}
    if not isinstance(c, dict) or not required <= set(c) or set(c)-required-{'producer_policy','uiux_policy','quality_policy'}:
        raise WorkflowError('config requires exactly ' + ', '.join(sorted(required)))
    repo = Path(c['repo']).resolve()
    metadata = repo / '.git'
    checkout = repo.is_dir() and metadata.is_dir()
    if not checkout and repo.is_dir() and metadata.is_file() and not metadata.is_symlink():
        # Linked worktrees use a gitfile. Verify the selected root with Git;
        # do not inherit GIT_DIR/GIT_WORK_TREE, credentials or user Git settings.
        environment = {'PATH': os.defpath + ':/opt/homebrew/bin:/usr/local/bin',
                       'LC_ALL': 'C', 'GIT_CONFIG_GLOBAL': os.devnull,
                       'GIT_CONFIG_NOSYSTEM': '1', 'GIT_OPTIONAL_LOCKS': '0'}
        try:
            result = subprocess.run(['git', '-C', str(repo), 'rev-parse', '--show-toplevel'],
                                    env=environment, capture_output=True, text=True, timeout=10)
            selected_root = result.stdout.rstrip('\r\n')
            checkout = result.returncode == 0 and bool(selected_root) and Path(selected_root).resolve() == repo
        except (OSError, ValueError, subprocess.TimeoutExpired):
            checkout = False
    if not checkout:
        raise WorkflowError('repo must be a local git checkout')
    if not isinstance(c['tests'], list) or not c['tests']:
        raise WorkflowError('tests must be a nonempty argv list')
    for argv in c['tests']:
        if not isinstance(argv, list) or not argv or not all(isinstance(a, str) and a for a in argv):
            raise WorkflowError('test command must be nonempty string argv')
    if c['sources'] != ['local']:
        raise WorkflowError('only local source is implemented')
    b = c['budget']
    if not isinstance(b, dict) or set(b) != {'lease_seconds', 'run_seconds', 'test_seconds', 'max_attempts'}:
        raise WorkflowError('invalid budget keys')
    if any(type(v) is not int or v <= 0 for v in b.values()):
        raise WorkflowError('budgets must be positive integers')
    if b['lease_seconds'] > b['run_seconds'] or b['test_seconds'] > b['run_seconds']:
        raise WorkflowError('lease/test budget exceeds run budget')
    if (not isinstance(c['acceptance'], dict)
            or any(type(v) is not bool for v in c['acceptance'].values())
            or c['acceptance'] != {'require_tests': True, 'require_unchanged_candidate': True}):
        raise WorkflowError('mandatory acceptance checks cannot be disabled')
    if (not isinstance(c['permissions'], dict)
            or any(type(v) is not bool for v in c['permissions'].values())
            or c['permissions'] != {'execute_tests': True, 'publish': False, 'network': False}):
        raise WorkflowError('only local test execution is permitted')
    if 'producer_policy' in c:
        d = c['producer_policy']
        if not isinstance(d, dict) or set(d) != {'path', 'sha256'} or not isinstance(d['path'], str) or not Path(d['path']).is_absolute() or not isinstance(d['sha256'], str) or len(d['sha256']) != 64 or any(x not in '0123456789abcdef' for x in d['sha256']):
            raise WorkflowError('invalid owner producer descriptor')
        path = Path(d['path']).resolve()
        if path == repo or repo in path.parents or not path.is_file() or Path(d['path']).is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != d['sha256']:
            raise WorkflowError('producer policy must be owner-bound outside checkout')
    if 'uiux_policy' in c:
        uiux_policy(c,repo)
    if 'quality_policy' in c:
        from .quality import load
        try: load(c)
        except (ValueError,TypeError,KeyError,OSError): raise WorkflowError('invalid owner quality policy') from None
    return dict(c, repo=str(repo))


def uiux_policy(config,repo):
    """Load the external immutable opt-in; task packets cannot supply authority."""
    descriptor=config.get('uiux_policy')
    if not isinstance(descriptor,dict) or set(descriptor)!={'path','sha256'}:
        raise WorkflowError('owner UI/UX descriptor required')
    path=descriptor['path'];sha=descriptor['sha256']
    if (not isinstance(path,str) or not Path(path).is_absolute()
            or not isinstance(sha,str) or len(sha)!=64 or any(x not in '0123456789abcdef' for x in sha)):
        raise WorkflowError('invalid owner UI/UX descriptor')
    file=Path(path);resolved=file.resolve();repo=Path(repo).resolve()
    if file.is_symlink() or not file.is_file() or resolved==repo or repo in resolved.parents:
        raise WorkflowError('UI/UX policy must be owner-bound outside checkout')
    from .uiux import read_regular,strict_json,validate_policy
    try:
        raw=read_regular(file,1048576)
        if hashlib.sha256(raw).hexdigest()!=sha:
            raise WorkflowError('owner UI/UX policy changed')
        return validate_policy(strict_json(raw))
    except ValueError as error:raise WorkflowError(str(error)) from error


def _capability_inventory(workflow=None):
    """Pure supported-interface metadata; no owner state or operational health query."""
    from .mcp import FIELDS, PROTOCOL_VERSIONS, SERVER_INFO
    return {
        'schema_version': 1,
        'package': dict(SERVER_INFO),
        'workflow_package_sha256': workflow if workflow is not None else digest(Path(__file__).parent),
        'interfaces': {'core': True, 'cli': True,
                       'mcp': {'transport': 'stdio', 'protocol_versions': list(PROTOCOL_VERSIONS),
                               'tools': sorted(FIELDS)}},
        'host': {'agent_dispatch': 'caller_supplied', 'autonomous_model_dispatch': False,
                 'model_dependency': False},
        'boundaries': {'read_only': True, 'claims_work': False, 'executes_commands': False,
                       'mutates_policy': False, 'production_visual_verified': False},
    }


class Engine:
    def __init__(self, state, config=None):
        self.state = Path(state).resolve()
        self.state.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.state / 'tasks.sqlite', timeout=10, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS policy (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, source TEXT, external_key TEXT,
          title TEXT, body TEXT, revision INTEGER, state TEXT, UNIQUE(source,external_key));
        CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, task_id TEXT, worker TEXT, token TEXT,
          state TEXT, started REAL, lease_until REAL, deadline REAL, policy TEXT, revision INTEGER,
          workflow TEXT, baseline TEXT, candidate TEXT, evidence TEXT);
        CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT,
          run_id TEXT, at REAL, kind TEXT, data TEXT);
        ''')
        with self.tx():
            row = self.db.execute('SELECT body FROM policy WHERE id=1').fetchone()
            if row is None:
                if config is None:
                    raise WorkflowError('initialize state with project configuration first')
                c = validate_config(config)
                self.db.execute('INSERT INTO policy VALUES(1,?)', (json.dumps(c, sort_keys=True),))
            else:
                c = json.loads(row[0])
                if config is not None and validate_config(config) != c:
                    raise WorkflowError('policy is frozen; create a separately reviewed project state')
        self.config = c
        self.workflow = digest(Path(__file__).parent)
        if c.get('producer_policy'):
            self.producer_init()

    def capabilities(self):
        """Describe this controller's supported interfaces without reconciling state."""
        return _capability_inventory(self.workflow)

    def next_step(self, task_id='', run_id='', **extra):
        """Observe advisory recovery guidance without reconciling stored state."""
        from .guidance import next_step
        return next_step(self.state, self.workflow, task_id, run_id, **extra)

    @contextlib.contextmanager
    def tx(self):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            yield
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK')
            raise

    def event(self, tid, rid, kind, data=None):
        self.db.execute('INSERT INTO events(task_id,run_id,at,kind,data) VALUES(?,?,?,?,?)',
                        (tid, rid, time.time(), kind, json.dumps(data or {}, sort_keys=True)))

    def producer_init(self):
        from .producer import init
        return init(self)

    def context_query(self, query, mandatory_paths='[]', byte_budget='16384', max_file_bytes='16384', if_none_match=''):
        from .context import query_context
        return query_context(self, query, mandatory_paths, byte_budget, max_file_bytes, if_none_match)

    def handoff_build(self, run_id, token, budget_bytes='16384'):
        from .handoff import build
        return build(self, run_id, token, budget_bytes)

    def handoff_read(self, handoff_id, include_stale='false'):
        from .handoff import read
        return read(self, handoff_id, include_stale)

    def experience_record(self, kind, run_id, statement='', proof_seq='', expires_seconds='86400'):
        from .experience import record
        return record(self,kind,run_id,statement,proof_seq,expires_seconds)

    def experience_query(self, source_digest='', failure_fingerprint='', include_stale='false', limit='20'):
        from .experience import query
        return query(self,source_digest,failure_fingerprint,include_stale,limit)

    def producer_offer(self, source, external_key, check_id, title, body='', source_ref=''):
        from .producer import offer
        return offer(self, source, external_key, check_id, title, body, source_ref)

    def producer_scan(self, limit=None):
        from .producer import scan
        return scan(self, limit)

    def producer_discover(self, limit=None):
        from .producer import discover
        return discover(self, limit)

    def queue_status(self):
        from .producer import queue
        return queue(self)

    def _uiux_task(self,proposal):
        fields={'external_key','title','body','commands','permissions','require_owner_approval'}
        if (not isinstance(proposal,dict) or set(proposal)!=fields
                or proposal['commands']!=[] or proposal['permissions']!={}
                or type(proposal['require_owner_approval']) is not bool
                or proposal['require_owner_approval']
                or not isinstance(proposal['body'],str)):
            raise WorkflowError('only owner-approved inert UI/UX investigations may import')
        uiux_policy(self.config,self.config['repo'])
        packet=self._uiux_json(proposal['body'])
        if not isinstance(packet,dict) or packet.get('source_sha256')!=digest(self.config['repo']):
            raise WorkflowError('stale UI/UX proposal source; discover a fresh run')
        body=json.dumps(proposal,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)
        return self.import_task(proposal['external_key'],proposal['title'],body,source='local')

    def _uiux_call(self,method,*args):
        from .uiux import UIUXStore
        policy=uiux_policy(self.config,self.config['repo'])
        store=None
        try:
            store=UIUXStore(self.state/'uiux',policy,task_sink=self._uiux_task)
            # A standalone Store cannot redirect this Core state's source.
            repo=Path(self.config['repo']).resolve()
            store._integrity()
            for run in store.db.execute('SELECT source_root FROM runs'):
                if run['source_root']!=str(repo):
                    raise WorkflowError('UI/UX run is bound to a different Core repository')
            return getattr(store,method)(*args)
        except ValueError as error:
            raise WorkflowError(str(error)) from error
        finally:
            if store is not None:store.db.close()

    @staticmethod
    def _uiux_json(value):
        from .uiux import strict_json
        if not isinstance(value,str):raise WorkflowError('UI/UX packet must be a JSON string')
        try:return strict_json(value)
        except ValueError as error:raise WorkflowError(str(error)) from error

    def uiux_init(self,policy):
        owner=uiux_policy(self.config,self.config['repo'])
        if self._uiux_json(policy)!=owner:
            raise WorkflowError('supplied UI/UX policy differs from owner descriptor')
        return self._uiux_call('status')

    def uiux_discover(self):
        if 'uiux_policy' not in self.config:
            return {'phase':'not_applicable','disabled':True,'production_visual_verified':False}
        result=self._uiux_call('discover',self.config['repo'])
        if result.get('run_id'):
            # Import after the Store's durable discovery. Replays after a crash
            # use exactly the same key/body, including unchanged/not_due runs.
            proposal=result['audit_proposal']
            if proposal['external_key']!='uiux:due:'+result['run_id']:
                raise WorkflowError('UI/UX due audit identity mismatch')
            self._uiux_task(proposal)
        return result

    def uiux_evidence(self,run_id,stage,evidence):
        uiux_policy(self.config,self.config['repo'])
        return self._uiux_call('record',run_id,stage,self._uiux_json(evidence))

    def uiux_review(self,run_id,reviewer,rubric,verdict,rationale,findings):
        uiux_policy(self.config,self.config['repo'])
        return self._uiux_call('review',run_id,reviewer,self._uiux_json(rubric),verdict,rationale,self._uiux_json(findings))

    def uiux_query(self):
        if 'uiux_policy' not in self.config:
            return {'phase':'not_applicable','disabled':True,'production_visual_verified':False}
        return self._uiux_call('query')

    def import_task(self, external_key, title, body='', source='local'):
        if source not in self.config['sources'] or not all(isinstance(x, str) and x for x in (external_key, title)):
            raise WorkflowError('invalid source or task fields')
        if not isinstance(body, str):
            raise WorkflowError('body must be a string')
        with self.tx():
            old = self.db.execute('SELECT * FROM tasks WHERE source=? AND external_key=?', (source, external_key)).fetchone()
            if old:
                if old['title'] != title or old['body'] != body:
                    raise WorkflowError('dedupe key conflicts with existing immutable task')
                return dict(old)
            tid = 'task-' + uuid.uuid4().hex[:12]
            self.db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,1,?)', (tid, source, external_key, title, body, 'queued'))
            self.event(tid, None, 'imported', {'source': source, 'external_key': external_key})
            return self.status(task_id=tid)

    def claim(self, worker, task_id=None):
        if not isinstance(worker, str) or not worker.strip():
            raise WorkflowError('worker must be named')
        now = time.time()
        with self.tx():
            self.reconcile()
            for r in self.db.execute("SELECT * FROM runs WHERE state IN ('running','changed','verifying','verified','ready_review') AND lease_until<=?", (now,)).fetchall():
                self.db.execute("UPDATE runs SET state='expired' WHERE id=?", (r['id'],))
                self.db.execute("UPDATE tasks SET state='queued' WHERE id=?", (r['task_id'],))
                self.event(r['task_id'], r['id'], 'expired')
            active = self.db.execute("SELECT id FROM runs WHERE state IN ('running','changed','verifying','verified','ready_review')").fetchone()
            if active:
                raise WorkflowError('checkout already leased: ' + active[0])
            b = self.config['budget']
            # Default pull must not let a terminally exhausted queue head
            # starve later eligible tasks. Explicit selection keeps its error.
            query = "SELECT * FROM tasks WHERE state='queued'"
            if task_id:
                query += ' AND id=?'
                params = (task_id,)
            else:
                query += ' AND (SELECT count(*) FROM runs WHERE task_id=tasks.id) < ?'
                params = (b['max_attempts'],)
            if 'quality_policy' in self.config and not task_id:
                from .quality import project
                choice,_ = project(self.config,self.db,self.workflow,worker)
                if not choice['claim_allowed']: raise WorkflowError('quality claim blocked: '+choice['reason'])
                query += ' AND id=?'
                params += (choice['next_task_id'],)
            row = self.db.execute(query + ' ORDER BY rowid LIMIT 1', params).fetchone()
            if not row:
                raise WorkflowError('no queued task')
            count = self.db.execute('SELECT count(*) FROM runs WHERE task_id=?', (row['id'],)).fetchone()[0]
            if count >= b['max_attempts']:
                raise WorkflowError('attempt budget exhausted')
            quality_admission = None
            if 'quality_policy' in self.config:
                from .quality import project
                quality, quality_admission = project(self.config,self.db,self.workflow,worker,row['id'])
                if not quality['claim_allowed'] or quality['next_task_id'] != row['id']:
                    raise WorkflowError('quality claim blocked: '+quality['reason'])
            rid, token = 'run-' + uuid.uuid4().hex[:12], secrets.token_hex(24)
            baseline = digest(self.config['repo'])
            self.db.execute('INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                            (rid, row['id'], worker, token, 'running', now, now+b['lease_seconds'], now+b['run_seconds'], json.dumps(self.config, sort_keys=True), row['revision'], self.workflow, baseline, None, None))
            self.db.execute("UPDATE tasks SET state='running' WHERE id=?", (row['id'],))
            self.event(row['id'], rid, 'claimed', {'worker': worker, 'workflow': self.workflow, 'baseline': baseline, 'revision': row['revision'], 'policy': self.config})
            if quality_admission is not None:
                self.event(row['id'],rid,'quality_admitted',quality_admission)
            return dict(self.db.execute('SELECT * FROM runs WHERE id=?', (rid,)).fetchone())

    def owned(self, run_id, token):
        r = self.db.execute('SELECT * FROM runs WHERE id=?', (run_id,)).fetchone()
        if not r or not secrets.compare_digest(r['token'], token):
            raise WorkflowError('invalid run fencing token')
        if self.workflow != r['workflow']:
            raise WorkflowError('controller version differs from claimed workflow')
        if r['state'] in ('accepted', 'failed', 'expired', 'invalidated') or time.time() >= min(r['lease_until'], r['deadline']):
            raise WorkflowError('inactive or expired lease')
        if r['state'] == 'verifying':
            raise WorkflowError('verification in progress; wait for result or lease recovery')
        if 'quality_policy' in self.config:
            from .quality import guard_owned
            guard_owned(self,r)
        return r

    def heartbeat(self, run_id, token):
        with self.tx():
            r = self.owned(run_id, token)
            until = min(time.time() + json.loads(r['policy'])['budget']['lease_seconds'], r['deadline'])
            self.db.execute('UPDATE runs SET lease_until=? WHERE id=?', (until, run_id))
            self.event(r['task_id'], run_id, 'heartbeat', {'lease_until': until})
            return {'lease_until': until}

    def progress(self, run_id, token, note):
        with self.tx():
            r = self.owned(run_id, token)
            self.event(r['task_id'], run_id, 'progress', {'note': note})
        return {'recorded': True}

    def change(self, run_id, token):
        with self.tx():
            r = self.owned(run_id, token)
            candidate = digest(json.loads(r['policy'])['repo'])
            self.db.execute("UPDATE runs SET state='changed',candidate=?,evidence=NULL WHERE id=?", (candidate, run_id))
            self.db.execute("UPDATE tasks SET state='changed' WHERE id=?", (r['task_id'],))
            self.event(r['task_id'], run_id, 'changed', {'candidate': candidate})
        return {'candidate': candidate}

    def verify(self, run_id, token):
        with self.tx():
            r = self.owned(run_id, token)
            if r['state'] != 'changed':
                raise WorkflowError('submit candidate before verification')
            from .producer import task_check
            producer_check = task_check(self, r['task_id'])
            policy = json.loads(r['policy'])
            if digest(policy['repo']) != r['candidate']:
                raise WorkflowError('candidate changed; submit again')
            self.db.execute("UPDATE runs SET state='verifying' WHERE id=?", (run_id,))
            self.db.execute("UPDATE tasks SET state='verifying' WHERE id=?", (r['task_id'],))
            self.event(r['task_id'], run_id, 'verification_started', {'candidate': r['candidate']})
        results = []
        cutoff = min(r['lease_until'], r['deadline'])
        # No inherited API keys, shells, network clients or release credentials.
        env = {'PATH': os.defpath + ':/opt/homebrew/bin:/usr/local/bin', 'PYTHONHASHSEED': '0', 'LANG': 'en_US.UTF-8'}
        commands = policy['tests'] + ([producer_check['argv']] if producer_check else [])
        for command_index, argv in enumerate(commands):
            execution_env = dict(env)
            if producer_check and command_index == len(policy['tests']):
                execution_env['PYTHONPATH'] = str(Path(__file__).parent.parent)
            start = time.time()
            if start >= cutoff:
                break
            p = None
            guardian = None
            drains = []
            stdout, stderr = b'', b''
            command_cutoff = min(cutoff, start + policy['budget']['test_seconds'])
            try:
                if os.name == 'posix':
                    p, guardian = core_process.launch(argv, policy['repo'], execution_env,
                        command_cutoff)
                else:
                    p = subprocess.Popen(argv, cwd=policy['repo'], env=execution_env, stdout=subprocess.PIPE,
                                         stderr=subprocess.PIPE)
                drains = [_OutputDrain(p.stdout), _OutputDrain(p.stderr)]
                # Readers exclusively own the pipes. communicate only waits for the
                # child, preserving timeout/error handling without buffering output.
                p.stdout = p.stderr = None
                for drain in drains:
                    drain.thread.start()
                try:
                    p.communicate(timeout=max(.001, command_cutoff - time.time()))
                    result = {'argv': argv, 'exit_code': p.returncode}
                    if p.returncode < 0 and time.time() >= command_cutoff:
                        result.update(exit_code=124, timeout=True)
                except subprocess.TimeoutExpired:
                    result = {'argv': argv, 'exit_code': 124,
                              'timeout': True}
                    try:
                        self.stop_test_process(p)
                        p.communicate()
                    except Exception as e:
                        result['execution_error'] = True
                        result['cleanup_error'] = type(e).__name__ + ': ' + str(e)
            except Exception as e:
                result = {'argv': argv, 'exit_code': 127, 'execution_error': True,
                          'internal_error': type(e).__name__ + ': ' + str(e)}
                if getattr(e, 'launch_cleanup_errors', None):
                    result['cleanup_error'] = '; '.join(e.launch_cleanup_errors)
            finally:
                if p is not None:
                    try:
                        self.stop_test_process(p)
                    except Exception as e:
                        result['execution_error'] = True
                        result['cleanup_error'] = type(e).__name__ + ': ' + str(e)
                    finally:
                        for drain in drains:
                            if drain.thread.ident is not None:
                                drain.thread.join()
                            if drain.error:
                                result['execution_error'] = True
                                result['capture_error'] = drain.error
                        for stream in [d.stream for d in drains]:
                            if stream is not None:
                                try:
                                    stream.close()
                                except Exception as e:
                                    result['execution_error'] = True
                                    result['stream_cleanup_error'] = type(e).__name__ + ': ' + str(e)
                if guardian is not None:
                    try:
                        guardian.close()
                    except Exception as e:
                        result['execution_error'] = True
                        result['guardian_cleanup_error'] = type(e).__name__ + ': ' + str(e)
            if drains:
                stdout, stderr = (bytes(d.prefix) for d in drains)
            result.update(self.output_evidence(stdout, stderr))
            for name, drain in zip(('stdout', 'stderr'), drains):
                result[name + '_total_bytes'] = drain.total
                result[name + '_truncated'] = drain.total > len(drain.prefix)
            if result.get('internal_error'):
                result['stderr'] += result['internal_error']
            result['duration_seconds'] = time.time()-start
            results.append(result)
        source_error = None
        try:
            source_unchanged = digest(policy['repo']) == r['candidate']
        except Exception as e:
            source_unchanged = False
            source_error = type(e).__name__ + ': ' + str(e)
        evidence = {'baseline': r['baseline'], 'candidate': r['candidate'], 'workflow': r['workflow'],
                    'task_revision': r['revision'], 'policy': policy, 'results': results,
                    'execution_cutoff': cutoff,
                    'source_unchanged': source_unchanged}
        if source_error:
            evidence['source_error'] = source_error
        if producer_check:
            from .producer import check_passed
            evidence['task_acceptance'] = {'binding': producer_check, 'passed': len(results)==len(commands) and check_passed(producer_check,results[-1],r['candidate'])}
        passed = (len(results) == len(commands)
                  and (not producer_check or evidence['task_acceptance']['passed'])
                  and all(x['exit_code'] == 0 and not x.get('execution_error') for x in results)
                  and evidence['source_unchanged'])
        with self.tx():
            current = self.db.execute('SELECT * FROM runs WHERE id=?', (run_id,)).fetchone()
            active = (current['state'] == 'verifying' and current['candidate'] == r['candidate']
                      and time.time() < cutoff)
            evidence['ownership_valid_at_finish'] = active
            if active:
                state = 'verified' if passed else 'changed'
                self.db.execute('UPDATE runs SET state=?,evidence=? WHERE id=?',
                                (state, json.dumps(evidence, sort_keys=True), run_id))
                self.db.execute('UPDATE tasks SET state=? WHERE id=?', (state, r['task_id']))
                kind = 'verification_passed' if passed else 'verification_failed'
            else:
                passed = False
                # Retain machine evidence on the old attempt without using its expired token
                # for a worker transition or touching an already claimed successor.
                self.db.execute('UPDATE runs SET evidence=? WHERE id=?',
                                (json.dumps(evidence, sort_keys=True), run_id))
                if current['state'] == 'verifying':
                    self.db.execute("UPDATE runs SET state='expired' WHERE id=?", (run_id,))
                    self.db.execute("UPDATE tasks SET state='queued' WHERE id=?", (r['task_id'],))
                    self.event(r['task_id'], run_id, 'expired')
                kind = 'verification_expired'
            self.event(r['task_id'], run_id, kind, evidence)
        return {'passed': passed, 'evidence': evidence}

    @staticmethod
    def output_evidence(stdout, stderr):
        """Lossless retained prefixes plus readable, explicitly decoded text."""
        stdout_total, stderr_total = len(stdout), len(stderr)
        stdout, stderr = stdout[:OUTPUT_CAP], stderr[:OUTPUT_CAP]
        return {'stdout': stdout.decode('utf-8', errors='replace'),
                'stderr': stderr.decode('utf-8', errors='replace'),
                'stdout_base64': base64.b64encode(stdout).decode('ascii'),
                'stderr_base64': base64.b64encode(stderr).decode('ascii'),
                'stdout_total_bytes': stdout_total, 'stderr_total_bytes': stderr_total,
                'stdout_truncated': stdout_total > len(stdout),
                'stderr_truncated': stderr_total > len(stderr),
                'output_encoding': 'utf-8', 'output_errors': 'replace'}

    @staticmethod
    def stop_test_process(process):
        """Reap the direct child and stop ordinary descendants in its POSIX group."""
        error = None
        try:
            if os.name == 'posix':
                os.killpg(process.pid, signal.SIGKILL)
            elif process.poll() is None:
                process.kill()
        except ProcessLookupError:
            pass
        except Exception as failure:
            error = failure
            # Reap even if group cleanup is denied; preserve that denial as
            # separate evidence rather than masking exit/timeout semantics.
            if process.poll() is None:
                process.kill()
        finally:
            process.wait()
        if error:
            raise error

    def transition(self, run_id, token, target):
        with self.tx():
            r = self.owned(run_id, token)
            if (r['state'], target) not in {('verified', 'ready_review'), ('ready_review', 'accepted')}:
                raise WorkflowError('unauthorized state transition')
            e = json.loads(r['evidence'] or '{}')
            from .producer import task_check
            bound = task_check(self, r['task_id'])
            if bound and (e.get('task_acceptance', {}).get('binding') != bound or e.get('task_acceptance', {}).get('passed') is not True):
                raise WorkflowError('producer task acceptance missing or failed')
            if not e or digest(json.loads(r['policy'])['repo']) != e['candidate']:
                raise WorkflowError('stale acceptance evidence')
            if target == 'accepted' and 'quality_policy' in self.config:
                from .quality import guard_accept
                guard_accept(self,r)
            self.db.execute('UPDATE runs SET state=? WHERE id=?', (target, run_id))
            self.db.execute('UPDATE tasks SET state=? WHERE id=?', (target, r['task_id']))
            self.event(r['task_id'], run_id, target, {'candidate': r['candidate']})
        return {'state': target, 'published': False}

    def fail(self, run_id, token, reason):
        if not isinstance(reason, str) or not reason.strip():
            raise WorkflowError('failure reason must be nonempty')
        with self.tx():
            r = self.owned(run_id, token)
            self.db.execute("UPDATE runs SET state='failed' WHERE id=?", (run_id,))
            self.db.execute("UPDATE tasks SET state='queued' WHERE id=?", (r['task_id'],))
            self.event(r['task_id'], run_id, 'failed', {'reason': reason})
        return {'state': 'failed', 'retry': 'queued'}

    def reconcile(self):
        """Never expose an old acceptance as current after source modification."""
        with (contextlib.nullcontext() if self.db.in_transaction else self.tx()):
            rows = self.db.execute("SELECT * FROM runs WHERE state IN ('accepted','verified','ready_review')").fetchall()
            if not rows:
                return
            current = digest(self.config['repo'])
            for r in rows:
                if r['candidate'] == current:
                    continue
                closed = r['state'] == 'accepted'
                run_state = 'invalidated' if closed else 'changed'
                task_state = 'queued' if closed else 'changed'
                self.db.execute('UPDATE runs SET state=?,evidence=NULL WHERE id=?', (run_state, r['id']))
                self.db.execute('UPDATE tasks SET state=? WHERE id=?', (task_state, r['task_id']))
                self.event(r['task_id'], r['id'], 'acceptance_invalidated',
                           {'previous_state': r['state'], 'previous_candidate': r['candidate'], 'current_source': current})

    def status(self, task_id=None, run_id=None):
        self.reconcile()
        if run_id:
            r = self.db.execute('SELECT * FROM runs WHERE id=?', (run_id,)).fetchone()
            if not r:
                raise WorkflowError('run not found')
            result = dict(r)
            result.pop('token')
            return result
        if task_id:
            r = self.db.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
            if not r:
                raise WorkflowError('task not found')
            return dict(r)
        return {'tasks': [dict(r) for r in self.db.execute('SELECT * FROM tasks ORDER BY rowid')]}

    def history(self, task_id):
        return {'events': [dict(r) for r in self.db.execute('SELECT * FROM events WHERE task_id=? ORDER BY seq', (task_id,))]}
