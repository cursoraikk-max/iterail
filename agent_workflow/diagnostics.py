"""Bounded, sanitized evidence for owned local fixture failures.

Projection reads existing journals without initializing controllers. Native execution
is an explicit local protocol fixture, never a provider/model invocation.
"""
import argparse
import ast
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import stat
import sqlite3
import subprocess
import sys
import tempfile
import types
import uuid
from unittest.mock import patch

_ID = re.compile(r'(?:call|job|run|task)-[0-9a-f]{12,64}\Z')
_HEAD = re.compile(r'[0-9a-f]{40,64}\Z')
_KINDS = {'backend:worker', 'backend:reviewer', 'machine-test', 'workflow:claim',
          'workflow:change', 'workflow:verify', 'workflow:transition', 'workflow:accept',
          'workflow:progress', 'workflow:fail', 'workflow:status'}
_EVENTS = {'claimed', 'changed', 'progress', 'verification_failed', 'verified',
           'transitioned', 'accepted', 'failed', 'expired', 'lease_expired',
           'initialized', 'queued', 'task_imported', 'stopped', 'resumed'}
_CAS = {'integration_intent', 'integration_recovered', 'integration_reverted_before_cas',
        'integration_dirty_conflict', 'complete', 'completion_receipt',
        'legacy_integration_unverified'}
_LOG_EVENTS = {'thread.started', 'turn.started', 'turn.completed', 'turn.failed',
               'item.started', 'item.updated', 'item.completed', 'error'}
_STATES = {'queued', 'running', 'review', 'ready_review', 'accepted', 'failed',
           'expired', 'cancelled', 'blocked', 'paused', 'complete', 'finished'}
_REASONS = {'timeout', 'output_limit', 'cancelled', 'stopped', 'spawn_error',
            'startup_timeout', 'idle_timeout', 'total_timeout'}
_FAILURES = {'backend_failure', 'infrastructure_blocked', 'budget', 'permissions',
             'authentication', 'quota', 'provider', 'integrity', 'unknown'}
_MAX_NODES = 20000
_ROWS = 128
_INPUT = 262144


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _limits(max_files, max_bytes):
    if type(max_files) is not int or not 1 <= max_files <= 256:
        raise ValueError('file budget must be between 1 and 256')
    if type(max_bytes) is not int or not 4096 <= max_bytes <= 1048576:
        raise ValueError('byte budget must be between 4096 and 1048576')


def _root(value, *, exists=False):
    path = Path(os.path.abspath(value))
    # Reject symlink ancestors before resolve; resolve must not grant authority.
    for parent in (path, *path.parents):
        if parent.is_symlink() and not (parent != path and str(parent) in {'/var', '/tmp'}
                                            and str(parent.resolve()) in {'/private/var', '/private/tmp'}):
            raise ValueError('symlink root or ancestor')
    if exists and not path.is_dir():
        raise ValueError('fixture root must be a directory')
    if path.exists() and not path.is_dir():
        raise ValueError('root must be a directory')
    return path.resolve()


def _roots(fixture, output):
    fixture, output = _root(fixture, exists=True), _root(output)
    if fixture == output or fixture.is_relative_to(output) or output.is_relative_to(fixture):
        raise ValueError('fixture and output roots overlap')
    return fixture, output


def _inventory(root):
    """Bound metadata separately from eligible evidence reads, without following links."""
    stack, dbs, projects, count = [root], [], [], 0
    while stack:
        directory = stack.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                count += 1
                if count > _MAX_NODES:
                    raise ValueError('fixture traversal budget exceeded')
                if entry.is_symlink():
                    raise ValueError('descendant symlink')
                path = Path(entry.path)
                if entry.is_dir(follow_symlinks=False):
                    if entry.name == '.git':
                        projects.append(directory)
                        stack.append(path)  # Bound and reject links in Git metadata too.
                    else:
                        stack.append(path)
                elif entry.is_file(follow_symlinks=False) and entry.name in {'runner.sqlite', 'tasks.sqlite'}:
                    dbs.append(path)
    return sorted(dbs), sorted(projects)


class _ReadBudget:
    def __init__(self, files):
        self.remaining = files
        self.bytes_left = 4194304

    def use(self, path, *, database=False):
        self.remaining -= 1
        if self.remaining < 0:
            raise ValueError('eligible evidence file budget exceeded')
        if path.is_symlink() or not path.is_file():
            raise ValueError('evidence file missing or symlink')
        size = path.stat().st_size
        if size > (16777216 if database else _INPUT):
            raise ValueError('evidence input size exceeded')
        if not database:
            self.bytes_left -= size
            if self.bytes_left < 0:
                raise ValueError('aggregate evidence input budget exceeded')

    def read(self, path):
        self.use(path)
        with path.open('rb') as stream:
            raw = stream.read(_INPUT + 1)
        if len(raw) > _INPUT:
            raise ValueError('evidence grew beyond input budget')
        return raw


def _db(path, budget):
    budget.use(path, database=True)
    db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    steps = [0]
    def bound():
        steps[0] += 1
        return steps[0] > 1000
    db.set_progress_handler(bound, 1000)
    return db


def _query(db, query):
    rows = db.execute(query + ' LIMIT ' + str(_ROWS + 1)).fetchall()
    if len(rows) > _ROWS:
        raise ValueError('journal row budget exceeded')
    return rows


def _json(raw):
    if not isinstance(raw, str) or len(raw) > _INPUT:
        return None
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else None
    except (ValueError, RecursionError):
        return None


def _identity(value):
    return value if isinstance(value, str) and _ID.fullmatch(value) else None


def _git(project, *args):
    # File-backed stdout keeps subprocess allocation bounded even for hostile Git output.
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        process = subprocess.Popen(['git', '--no-optional-locks', '-C', str(project), *args],
                                   stdout=stdout, stderr=stderr, stdin=subprocess.DEVNULL,
                                   env=dict(os.environ, GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull))
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait()
            raise ValueError('Git observation timeout')
        if process.returncode:
            raise ValueError('Git observation rejected')
        if stdout.tell() > _INPUT:
            raise ValueError('Git observation size exceeded')
        stdout.seek(0)
        return stdout.read(_INPUT + 1)


def _projection(root, *, error, initial_git, max_files):
    dbs, projects = _inventory(root)
    budget = _ReadBudget(max_files)
    record = {'schema_version': 1, 'complete': True, 'runner_calls': [], 'core_runs': [],
              'core_events': [], 'git': [], 'cycles': [],
              'error_sha256': _sha(str(error)[:65536].encode('utf-8', 'replace'))}
    cas = set()
    initials = {}
    for key, value in (initial_git or {}).items():
        project = _root(key, exists=True)
        if not project.is_relative_to(root) or project not in projects:
            raise ValueError('initial Git project outside owned fixture or not a Git project')
        if not isinstance(value, str) or not _HEAD.fullmatch(value):
            raise ValueError('invalid initial Git HEAD')
        initials[project] = value
    for path in dbs:
        db = _db(path, budget)
        try:
            if path.name == 'runner.sqlite':
                for event in _query(db, 'SELECT substr(kind,1,128) AS kind FROM events ORDER BY seq'):
                    if event['kind'] in _CAS:
                        cas.add(event['kind'])
                calls = _query(db, 'SELECT substr(id,1,128) AS id,substr(kind,1,128) AS kind,substr(phase,1,128) AS phase,CASE WHEN length(result)<=262144 THEN result END AS result FROM calls ORDER BY rowid')
                for row in calls:
                    terminal = _json(row['result'])
                    ident = _identity(row['id'])
                    item = {'kind': row['kind'] if row['kind'] in _KINDS else 'other',
                            'phase': row['phase'] if row['phase'] in _STATES else 'unknown',
                            'pid_identity_verified': False}
                    if ident:
                        item['id'] = ident
                    if terminal is None:
                        record['complete'] = False
                        record['runner_calls'].append(item); continue
                    for key in ('started', 'duration', 'total_deadline'):
                        if _number(terminal.get(key)):
                            item[key] = terminal[key]
                        else: record['complete'] = False
                    for key in ('exit_code', 'returncode'):
                        if type(terminal.get(key)) is int:
                            item[key] = terminal[key]
                    reason = terminal.get('reason')
                    item['reason'] = reason if isinstance(reason, str) and reason in _REASONS else (None if reason is None else 'unknown')
                    if isinstance(terminal.get('cwd'), str):
                        item['cwd_sha256'] = _sha(terminal['cwd'].encode())
                    # Only the known call-ID file under this journal's logs is authority.
                    logs = path.parent / 'logs'
                    log = logs / ((ident or 'invalid') + '.log')
                    pidfile = logs / ((ident or 'invalid') + '.pid')
                    output = terminal.get('output')
                    if (ident and isinstance(output, str) and Path(output).resolve() == log
                            and log.is_file() and pidfile.is_file()):
                        raw = budget.read(log)
                        pid = _json(budget.read(pidfile).decode('utf-8', 'replace'))
                        item['log_sha256'], item['log_bytes'] = _sha(raw), len(raw)
                        events = set()
                        for line in raw.splitlines():
                            if len(line) > 65536: continue
                            try:
                                event = json.loads(line)
                                if isinstance(event, dict) and isinstance(event.get('type'), str) and event['type'] in _LOG_EVENTS:
                                    events.add(event['type'])
                            except (ValueError, RecursionError): pass
                        item['log_event_types'] = sorted(events)
                        bound_log = (terminal.get('saved_sha256') == item['log_sha256']
                                     and type(terminal.get('saved_bytes')) is int
                                     and terminal['saved_bytes'] == len(raw)
                                     and terminal.get('truncated') is False)
                        valid = (pid is not None and bound_log and terminal.get('call_id') == ident
                                 and pid.get('call_id') == ident
                                 and type(terminal.get('child_pid')) is int
                                 and type(terminal.get('guardian_pid')) is int
                                 and type(pid.get('pid')) is int and type(pid.get('guardian')) is int
                                 and terminal['child_pid'] > 0 and terminal['guardian_pid'] > 0
                                 and terminal['child_pid'] == pid['pid']
                                 and terminal['guardian_pid'] == pid['guardian']
                                 and _number(pid.get('started')) and _number(terminal.get('started'))
                                 and pid['started'] == terminal['started']
                                 and terminal.get('group_termination_confirmed') is True
                                 and terminal.get('cleanup_errors') == [])
                        item['pid_identity_verified'] = bool(valid)
                        if not valid: record['complete'] = False
                    else:
                        record['complete'] = False
                    record['runner_calls'].append(item)
            else:
                for row in _query(db, "SELECT substr(id,1,128) AS id,substr(task_id,1,128) AS task_id,substr(state,1,128) AS state,CASE WHEN typeof(started) IN ('integer','real') THEN started END AS started,CASE WHEN typeof(lease_until) IN ('integer','real') THEN lease_until END AS lease_until,CASE WHEN typeof(deadline) IN ('integer','real') THEN deadline END AS deadline FROM runs ORDER BY rowid"):
                    item = {'state': row['state'] if row['state'] in _STATES else 'unknown'}
                    if item['state'] == 'unknown': record['complete'] = False
                    for key in ('id', 'task_id'):
                        if _identity(row[key]): item[key] = row[key]
                    for key in ('started', 'lease_until', 'deadline'):
                        if _number(row[key]): item[key] = row[key]
                        else: record['complete'] = False
                    record['core_runs'].append(item)
                for row in _query(db, 'SELECT substr(kind,1,128) AS kind,substr(task_id,1,128) AS task_id,substr(run_id,1,128) AS run_id FROM events ORDER BY seq'):
                    if row['kind'] not in _EVENTS: continue
                    item = {'kind': row['kind']}
                    for key in ('task_id', 'run_id'):
                        if _identity(row[key]): item[key] = row[key]
                    record['core_events'].append(item)
        except sqlite3.Error:
            record['complete'] = False
        finally:
            db.close()
    for project in projects:
        try:
            final = _git(project, 'rev-parse', 'HEAD').decode().strip()
            if not _HEAD.fullmatch(final): raise ValueError('invalid Git HEAD')
            status = _git(project, '-c', 'core.fsmonitor=false', 'status', '--porcelain=v1', '-z', '--untracked-files=no')
            entries = status.split(b'\0')
            paths = [entry[3:] for entry in entries if len(entry) >= 3]
            record['git'].append({'project_sha256': _sha(str(project).encode()),
                                  'initial_head': initials.get(project), 'final_head': final,
                                  'status_entries': len(paths), 'status_paths_sha256': [_sha(p) for p in paths],
                                  'cas_events': sorted(cas)})
        except (ValueError, OSError):
            record['complete'] = False
    return record


def _commit(record, output, max_bytes):
    raw = (json.dumps(record, sort_keys=True, indent=2, allow_nan=False) + '\n').encode()
    if len(raw) > max_bytes:
        raise ValueError('sanitized output byte budget exceeded')
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = output / ('failure-' + uuid.uuid4().hex + '.json')
    try:
        with path.open('xb') as stream:
            os.chmod(path, 0o600)
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return {'artifact': str(path), 'artifact_sha256': _sha(raw)}


def retain_failure(fixture_root, output_root, *, error, initial_git=None,
                   max_files=64, max_bytes=262144):
    """Commit one sanitized failure; references in journals never grant read authority."""
    _limits(max_files, max_bytes)
    root, output = _roots(fixture_root, output_root)
    record = _projection(root, error=error, initial_git=initial_git, max_files=max_files)
    return _commit(record, output, max_bytes)


def _cleanup_owned(root, identity):
    """Bounded descriptor-relative deletion; only owned real directories gain permissions."""
    count = [0]
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW

    def directory(parent, name, expected, depth):
        if depth > 128:
            raise ValueError('owned cleanup depth budget exceeded')
        observed = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if not stat.S_ISDIR(observed.st_mode) or (observed.st_dev, observed.st_ino) != expected:
            raise ValueError('owned cleanup directory identity changed')
        # No chmod of parents, regular files, output, or symlink targets.
        os.chmod(name, stat.S_IMODE(observed.st_mode) | 0o700,
                 dir_fd=parent, follow_symlinks=False)
        fd = os.open(name, flags, dir_fd=parent)
        try:
            bound = os.fstat(fd)
            if (bound.st_dev, bound.st_ino) != expected:
                raise ValueError('owned cleanup opened directory identity changed')
            with os.scandir(fd) as entries:
                for entry in entries:
                    count[0] += 1
                    if count[0] > _MAX_NODES:
                        raise ValueError('owned cleanup traversal budget exceeded')
                    child = entry.stat(follow_symlinks=False)
                    if stat.S_ISDIR(child.st_mode):
                        directory(fd, entry.name, (child.st_dev, child.st_ino), depth + 1)
                    else:
                        os.unlink(entry.name, dir_fd=fd)
        finally:
            os.close(fd)
        current = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if (current.st_dev, current.st_ino) != expected:
            raise ValueError('owned cleanup final directory identity changed')
        os.rmdir(name, dir_fd=parent)

    try:
        parent = os.open(root.parent, flags)
        try:
            directory(parent, root.name, identity, 0)
        finally:
            os.close(parent)
        return {'cleanup_complete': True}
    except Exception as error:
        category = ('permission' if isinstance(error, PermissionError) else
                    'bounds_or_identity' if isinstance(error, ValueError) else 'io')
        return {'cleanup_complete': False,
                'cleanup_error': {'category': category,
                                  'error_sha256': _sha(str(error)[:65536].encode('utf-8', 'replace'))}}


def _run_owned(callback, output_root, *, max_files, max_bytes, context=None):
    _limits(max_files, max_bytes)
    output = _root(output_root)
    root = Path(tempfile.mkdtemp(prefix='iterail-diagnostic-')).resolve()
    os.chmod(root, 0o700)
    owned = root.stat()
    identity = (owned.st_dev, owned.st_ino)
    try:
        try:
            callback(root)
        except Exception as error:
            _, output = _roots(root, output)
            record = _projection(root, error=error, initial_git=(context or {}).get('initial_git'), max_files=max_files)
            record['cycles'] = (context or {}).get('cycles', [])
            record.update((context or {}).get('binding', {}))
            result = _commit(record, output, max_bytes)
            result['passed'] = False
        else:
            result = {'passed': True, 'artifact': None}
    finally:
        cleanup = _cleanup_owned(root, identity)
    result.update(cleanup)
    if not result['cleanup_complete']:
        result['passed'] = False
    return result


def run_fixture(callback, output_root, *, max_files=64, max_bytes=262144):
    """Run one owned disposable callback and clean raw data after sanitized capture."""
    return _run_owned(callback, output_root, max_files=max_files, max_bytes=max_bytes)


def run_native_target(trusted_dir, source_root, output_root, *, delay_seconds=0,
                      max_files=64, max_bytes=262144):
    """Execute the unchanged original native target assertions, with optional fixture delay."""
    if not _number(delay_seconds) or not 0 <= delay_seconds <= 10:
        raise ValueError('fixture delay must be between zero and ten seconds')
    trusted, source = _root(trusted_dir, exists=True), _root(source_root, exists=True)
    from . import evolution
    from .core import digest
    pins = {name: _sha((trusted / name).read_bytes()) for name in ('target.py', 'common.py', 'native_backend.py')}
    before = digest(source)
    binding = {'diagnostic_module_sha256': _sha(Path(__file__).read_bytes()),
               'subject_source_sha256': before, 'trusted_sha256': pins,
               'call_cap_seconds': 2, 'execution_kind': 'native_fixture',
               'external_provider_calls': 0, 'successful_model_turns': 0,
               'historical_failure_cause': 'unknown', 'injected_delay_seconds': delay_seconds}
    context = {'initial_git': {}, 'cycles': [], 'binding': binding}

    def callback(root):
        instances = []
        spec = importlib.util.spec_from_file_location('_iterail_diagnostic_common', trusted / 'common.py')
        common = importlib.util.module_from_spec(spec); spec.loader.exec_module(common)
        common.REPO = source
        class Directory:
            def __init__(self, *args, **kwargs):
                self.name = str(root / 'fixture')
                Path(self.name).mkdir(mode=0o700)
            def cleanup(self): pass  # Outer lifecycle commits evidence before deleting raw files.
        class Captured(evolution.Supervisor):
            def __init__(self, owner_root, config, *args, **kwargs):
                if config['budget']['call_seconds'] != 2:
                    raise ValueError('original native call budget changed')
                project = Path(config['repo'])
                context['initial_git'][str(project)] = _git(project, 'rev-parse', 'HEAD').decode().strip()
                super().__init__(owner_root, config, *args, **kwargs); instances.append(self)
            def cycle(self):
                result = super().cycle()
                item = {}
                if isinstance(result, dict):
                    if result.get('phase') in _STATES or result.get('phase') in {'idle', 'backoff'}:
                        item['phase'] = result['phase']
                    if result.get('failure_class') in _FAILURES:
                        item['failure_class'] = result['failure_class']
                context['cycles'].append(item)
                return result
        def hook(backend):
            if not delay_seconds: return
            original = backend.parent / 'backend-original.py'
            original.write_bytes(backend.read_bytes()); original.chmod(0o700)
            backend.write_text('#!' + sys.executable + '\nimport os,sys,time\n'
                               + "if sys.argv[1]=='worker':\n print('NATIVE_FIXTURE_INJECTED_DELAY',flush=True)\n time.sleep("
                               + repr(delay_seconds) + ')\n'
                               + 'os.execv(sys.executable,[sys.executable,' + repr(str(original)) + ',*sys.argv[1:]])\n')
            backend.chmod(0o700)
        tree = ast.parse((trusted / 'target.py').read_text())
        tree.body = [node for node in tree.body if not (isinstance(node, ast.Expr)
                     and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name)
                     and node.value.func.id == 'run')]
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'native_target')
        body = function.body[1].body
        at = next(i for i, node in enumerate(body) if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
                  and isinstance(node.value.func, ast.Attribute) and node.value.func.attr == 'chmod'
                  and isinstance(node.value.func.value, ast.Name) and node.value.func.value.id == 'backend')
        body.insert(at + 1, ast.Expr(value=ast.Call(func=ast.Name(id='_hook_backend', ctx=ast.Load()),
                                                  args=[ast.Name(id='backend', ctx=ast.Load())], keywords=[])))
        ast.fix_missing_locations(tree)
        namespace = {'__file__': str(trusted / 'target.py'), '_hook_backend': hook}
        previous = sys.modules.get('common')
        sys.modules['common'] = common
        try:
            exec(compile(tree, str(trusted / 'target.py'), 'exec'), namespace)
            with patch.object(common.tempfile, 'TemporaryDirectory', Directory):
                namespace['native_target'](types.SimpleNamespace(Supervisor=Captured))
        finally:
            for owner in instances: owner.db.close()
            if previous is None: sys.modules.pop('common', None)
            else: sys.modules['common'] = previous
            binding['subject_source_unchanged'] = before == digest(source)
            binding['trusted_unchanged'] = all(_sha((trusted / name).read_bytes()) == value for name, value in pins.items())
            if not binding['subject_source_unchanged'] or not binding['trusted_unchanged']:
                raise ValueError('native source binding changed')
    result = _run_owned(callback, output_root, max_files=max_files, max_bytes=max_bytes, context=context)
    return dict(result, **binding)



def host_contract():
    """Describe existing inert bridge packets; structural validity grants no authority."""
    return {'schema_version': 1, 'request_kinds': ['worker', 'review', 'quality_review'],
            'authority': 'structural_only', 'autonomous_dispatch': False, 'model_dependency': False}


def _host_path(value):
    path = Path(value)
    if not path.is_absolute() or any(p.is_symlink() for p in (path, *path.parents)) or str(path.resolve()) != os.fspath(value):
        raise ValueError('invalid host path')
    return path


def validate_host_request(packet, expected_version=None):
    """Check a packet's shape and optional physical identity, never dispatch or authorize."""
    try:
        base = {'id', 'kind', 'task_id', 'run_id', 'version', 'controller_path', 'package_sha256', 'source_sha256'}
        if not isinstance(packet, dict) or packet.get('kind') not in host_contract()['request_kinds']:
            raise ValueError('packet kind')
        semantic = packet['kind'] == 'quality_review'
        extra = {'core_policy_sha256', 'quality_policy_sha256', 'projection_request_id'} if semantic else set()
        if set(packet) != base | extra: raise ValueError('packet keys')
        def matches(value, pattern):
            return isinstance(value, str) and re.fullmatch(pattern, value) is not None
        if not matches(packet['id'], r'request-[0-9a-f]{24}') or not matches(packet['version'], r'version-[0-9a-f]{24}'):
            raise ValueError('packet identity')
        for key in {'package_sha256', 'source_sha256'} | extra:
            if not matches(packet[key], r'[0-9a-f]{64}'): raise ValueError('packet hash')
        if semantic:
            if packet['task_id'] is not None or packet['run_id'] is not None: raise ValueError('semantic identity')
        else:
            if not matches(packet['task_id'], r'task-[0-9a-f]{12,64}'): raise ValueError('task identity')
            if packet['run_id'] is None:
                if packet['kind'] != 'worker': raise ValueError('review run required')
            elif not matches(packet['run_id'], r'run-[0-9a-f]{12,64}'): raise ValueError('run identity')
        _host_path(packet['controller_path'])
        if expected_version is not None:
            if not isinstance(expected_version, dict) or any(packet[key] != expected_version[target] for key, target in
                    [('version', 'id'), ('controller_path', 'controller_path'), ('package_sha256', 'package_sha256')]):
                raise ValueError('physical version mismatch')
        return {'kind': packet['kind'], 'request_id': packet['id'], **{key: packet[key] for key in
                ('task_id', 'run_id', 'version', 'package_sha256', 'source_sha256')}}
    except (ValueError, TypeError, KeyError, OSError, RecursionError):
        raise ValueError('invalid host request') from None


def _host_executable(value, expected):
    result = {'status': 'not_requested', 'sha256': None, 'size': None}
    if value is None:
        if expected is not None: raise ValueError('executable required for expected hash')
        return result
    path = Path(value)
    if any(p.is_symlink() for p in (path, *path.parents)):
        return {**result, 'status': 'symlink_rejected'}
    path = _host_path(value)
    try: info = path.stat()
    except FileNotFoundError: return {**result, 'status': 'missing'}
    result['size'] = info.st_size
    if not stat.S_ISREG(info.st_mode): return {**result, 'status': 'not_regular'}
    if not info.st_mode & 0o111: return {**result, 'status': 'not_executable'}
    if info.st_size > 16777216: return {**result, 'status': 'too_large'}
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, 'O_NOFOLLOW', 0))
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode): return {**result, 'status': 'not_regular', 'size': opened.st_size}
        if not opened.st_mode & 0o111: return {**result, 'status': 'not_executable', 'size': opened.st_size}
        if opened.st_size > 16777216: return {**result, 'status': 'too_large', 'size': opened.st_size}
        digest = hashlib.sha256(); size = 0
        while chunk := os.read(fd, 65536):
            size += len(chunk)
            if size > 16777216: return {**result, 'status': 'too_large', 'size': size}
            digest.update(chunk)
        final = os.fstat(fd)
        if (opened.st_size, opened.st_mtime_ns) != (final.st_size, final.st_mtime_ns) or size != opened.st_size:
            raise ValueError('static file changed')
        value = digest.hexdigest()
        return {'status': 'hash_mismatch' if expected is not None and value != expected else 'metadata_only', 'sha256': value, 'size': size}
    finally: os.close(fd)


def _host_ipc(root):
    import errno
    import socket
    def probe(family, address):
        if family is None: return {'status': 'unsupported', 'errno': None}
        try:
            with socket.socket(family, socket.SOCK_STREAM) as sock:
                sock.settimeout(.25); sock.bind(address); sock.listen(1)
            return {'status': 'available', 'errno': None}
        except OSError as error:
            code = error.errno if type(error.errno) is int else None
            unsupported = {getattr(errno, key, None) for key in ('EAFNOSUPPORT', 'EPROTONOSUPPORT', 'ENOSYS', 'ENOTSUP')}
            return {'status': 'unsupported' if code is not None and code in unsupported else 'blocked', 'errno': code}
    with tempfile.TemporaryDirectory(prefix='iterail-ipc-', dir=root) as owned:
        return {'requested': True, 'loopback_tcp': probe(socket.AF_INET, ('127.0.0.1', 0)),
                'unix': probe(getattr(socket, 'AF_UNIX', None), str(Path(owned)/'ipc'))}


def host_readiness(source_root, executable_path=None, probe_ipc=False, probe_root=None, expected_executable_sha256=None):
    """Bounded local observation only; never inspect auth, run a command or connect."""
    try:
        if type(probe_ipc) is not bool: raise ValueError('probe flag')
        if expected_executable_sha256 is not None and (not isinstance(expected_executable_sha256, str)
                or re.fullmatch(r'[0-9a-f]{64}', expected_executable_sha256) is None): raise ValueError('expected hash')
        source = _host_path(source_root); _root(source, exists=True)
        package = source/'agent_workflow'
        if not package.is_dir(): raise ValueError('source package missing')
        root = None
        if probe_ipc:
            if probe_root is None: raise ValueError('explicit external probe root required')
            root = _host_path(probe_root); _root(root, exists=True)
            if root == source or root.is_relative_to(source): raise ValueError('probe root inside source')
        from .guidance import _source
        source_sha, package_sha = _source(source), _source(package)
        executable = _host_executable(executable_path, expected_executable_sha256)
        absent = {'status': 'not_requested', 'errno': None}
        ipc = _host_ipc(root) if probe_ipc else {'requested': False, 'loopback_tcp': dict(absent), 'unix': dict(absent)}
        return {'schema_version': 1, 'source_sha256': source_sha, 'package_sha256': package_sha,
                'executable': executable, 'ipc': ipc, 'model_calls': 0, 'provider_ready': False,
                'provider_readiness': 'not_tested', 'autonomous_dispatch': False, 'executes_commands': False}
    except (ValueError, TypeError, KeyError, OSError, RecursionError):
        raise ValueError('host readiness observation rejected') from None


def _host_main(argv):
    class Parser(argparse.ArgumentParser):
        def error(self, message): raise ValueError('invalid host readiness arguments')
    parser = Parser(description='Static host contract readiness; no model or executable invocation.')
    parser.add_argument('--source-root', required=True); parser.add_argument('--executable')
    parser.add_argument('--probe-ipc', action='store_true'); parser.add_argument('--probe-root')
    parser.add_argument('--expected-executable-sha256')
    try:
        args = parser.parse_args(argv)
        result = host_readiness(args.source_root, args.executable, args.probe_ipc, args.probe_root, args.expected_executable_sha256)
        print(json.dumps({'ok': True, 'result': result}, sort_keys=True)); return 0
    except Exception:
        print(json.dumps({'ok': False, 'error': 'host readiness observation rejected'})); return 1

def main():
    if sys.argv[1:2] == ['host-readiness']: return _host_main(sys.argv[2:])
    parser = argparse.ArgumentParser(description='Sanitized local native fixture diagnostics; no provider/model turns.')
    parser.add_argument('trusted_dir'); parser.add_argument('source_root'); parser.add_argument('output_root')
    parser.add_argument('--delay-seconds', type=float, default=0)
    parser.add_argument('--max-files', type=int, default=64); parser.add_argument('--max-bytes', type=int, default=262144)
    args = parser.parse_args()
    result = run_native_target(args.trusted_dir, args.source_root, args.output_root,
                               delay_seconds=args.delay_seconds, max_files=args.max_files, max_bytes=args.max_bytes)
    print(json.dumps(result, sort_keys=True))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
