"""Durable owner-bound requests for an explicitly external conversation bridge."""
import argparse
import contextlib
import copy
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import threading
import time
import uuid


class ContinuationError(ValueError):
    pass


def require(value, message):
    if not value: raise ContinuationError(message)


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def sha(value):
    return hashlib.sha256(value).hexdigest()


def load(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'duplicate owner JSON member')
            result[key] = value
        return result
    def constant(value): raise ContinuationError('nonfinite owner JSON')
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def regular(path, limit=1048576):
    path = Path(path)
    require(not path.is_symlink() and stat.S_ISREG(path.lstat().st_mode), 'regular owner file required')
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    with os.fdopen(fd, 'rb') as stream:
        require(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), 'regular owner file required')
        raw = stream.read(limit + 1)
    require(len(raw) <= limit, 'owner file byte ceiling exceeded')
    return raw


def digest(root):
    root = Path(root)
    require(root.is_dir() and not root.is_symlink() and root.resolve() == root, 'regular source directory required')
    h = hashlib.sha256()
    for path in sorted(root.rglob('*')):
        rel = path.relative_to(root)
        if any(x in {'.git', '.workflow', '__pycache__'} for x in rel.parts) or path.suffix == '.pyc': continue
        require(not path.is_symlink(), 'source symlink unsupported')
        if path.is_file():
            h.update(str(rel).encode() + b'\0')
            with path.open('rb') as stream:
                while chunk := stream.read(65536): h.update(chunk)
            h.update(b'\0')
    return h.hexdigest()


def absolute(value):
    require(isinstance(value, str) and Path(value).is_absolute() and not Path(value).is_symlink()
            and str(Path(value).resolve()) == value, 'absolute owner path without symlinks required')
    return Path(value)


def hash_value(value):
    return isinstance(value, str) and len(value) == 64 and all(x in '0123456789abcdef' for x in value)


def validate_policy(value, state):
    require(isinstance(value, dict) and set(value) == {'schema_version', 'repo', 'core_state', 'core_policy',
            'runtime_root', 'bootstrap', 'cadence', 'budget', 'adapter', 'external_provider_authorized'}, 'exact continuation owner policy required')
    p = copy.deepcopy(value)
    require(type(p['schema_version']) is int and p['schema_version'] == 1
            and p['adapter'] == 'conversation_pull' and p['external_provider_authorized'] is False,
            'conversation pull requires no external provider grant')
    repo, core, runtime = (absolute(p[k]) for k in ('repo', 'core_state', 'runtime_root'))
    require(repo.is_dir() and (repo/'.git').is_dir() and core.is_dir() and runtime.is_dir(), 'existing repo, Core and runtime roots required')
    require(all(not path.is_relative_to(repo) for path in (state, core, runtime)), 'owner state and runtime must be outside editable repo')
    descriptor = p['core_policy']
    require(isinstance(descriptor, dict) and set(descriptor) == {'path', 'sha256'} and hash_value(descriptor['sha256']), 'exact frozen Core policy descriptor required')
    policy_path = absolute(descriptor['path'])
    require(not policy_path.is_relative_to(repo), 'Core policy must be outside editable repo')
    bootstrap = p['bootstrap']
    require(isinstance(bootstrap, dict) and set(bootstrap) == {'controller_path', 'package_sha256', 'source_sha256', 'head'}
            and hash_value(bootstrap['package_sha256']) and hash_value(bootstrap['source_sha256'])
            and isinstance(bootstrap['head'], str) and len(bootstrap['head']) in (40, 64)
            and all(x in '0123456789abcdef' for x in bootstrap['head']), 'exact reviewed bootstrap required')
    controller = absolute(bootstrap['controller_path'])
    require(controller != runtime and controller.is_relative_to(runtime) and controller.is_dir(), 'controller must be in owned runtime root')
    cadence = p['cadence'];budget = p['budget']
    require(isinstance(cadence, dict) and set(cadence) == {'interval_seconds', 'backoff_seconds', 'max_backoff_seconds'}
            and all(type(x) is int and x > 0 for x in cadence.values())
            and cadence['backoff_seconds'] <= cadence['max_backoff_seconds'], 'positive bounded cadence required')
    require(isinstance(budget, dict) and set(budget) == {'wall_seconds', 'max_requests'}
            and all(type(x) is int and x > 0 for x in budget.values()), 'positive immutable resource ceilings required')
    return p


class Continuation:
    def __init__(self, state, policy=None, *, clock=time.time):
        self.root = absolute(str(Path(state).absolute()))
        self.clock = clock;self.lock = threading.RLock();self.db = None
        self.path = self.root/'continuation.sqlite';self.identity = self.root/'identity.json'
        existed = self.path.exists()
        require(existed or not self.root.exists() or not any(self.root.iterdir()), 'initialized continuation history missing')
        require(existed or policy is not None, 'owner continuation policy required for bootstrap')
        try:
            if existed:
                require(not self.path.is_symlink() and self.path.is_file() and self.identity.is_file(), 'initialized continuation identity missing')
                self.db = sqlite3.connect(self.path, isolation_level=None, timeout=10, check_same_thread=False)
                self.db.row_factory = sqlite3.Row
                row = self.db.execute('SELECT * FROM meta WHERE id=1').fetchone()
                require(row is not None, 'initialized continuation metadata missing')
                self.policy = validate_policy(load(row['policy']), self.root)
                self.policy_sha256 = sha(encoded(self.policy))
                require(policy is None or validate_policy(policy, self.root) == self.policy, 'continuation policy is immutable')
                self._identity()
            else:
                self.policy = validate_policy(policy, self.root);self.policy_sha256 = sha(encoded(self.policy))
                self._core_policy()
                version = self._version(self.policy['bootstrap'])
                self._runtime(version)
                require(digest(Path(self.policy['repo'])) == version['source_sha256']
                        and self._git('rev-parse', 'HEAD') == version['head'] and not self._git('status', '--porcelain'),
                        'bootstrap requires a clean exact reviewed checkout')
                started = self._now();self.root.mkdir(parents=True, exist_ok=True)
                self.db = sqlite3.connect(self.path, isolation_level=None, timeout=10, check_same_thread=False)
                self.db.row_factory = sqlite3.Row
                self.db.executescript('''CREATE TABLE meta(id INTEGER PRIMARY KEY CHECK(id=1),policy TEXT,policy_sha256 TEXT,started REAL,deadline REAL,requests_spent INTEGER,stopped INTEGER,stop_reason TEXT,phase TEXT,reason TEXT,next_due REAL,failure_count INTEGER,active_version TEXT,request TEXT);
                    CREATE TABLE events(seq INTEGER PRIMARY KEY AUTOINCREMENT,at REAL,kind TEXT,body TEXT);''')
                with self._tx():
                    self.db.execute('INSERT INTO meta VALUES(1,?,?,?,?,0,0,NULL,?,?,0,0,?,NULL)',
                                    (encoded(self.policy).decode(), self.policy_sha256, started, started+self.policy['budget']['wall_seconds'],
                                     'ready', 'reviewed owner bootstrap', encoded(version).decode()))
                    self._event('initialized', {'active_version': version})
                self.identity.write_bytes(encoded({'schema_version': 1, 'policy_sha256': self.policy_sha256}))
                self.identity.chmod(0o600)
        except BaseException:
            if self.db is not None: self.db.close()
            raise

    def _now(self):
        now = self.clock()
        require(type(now) in (int, float) and math.isfinite(now), 'finite continuation clock required')
        return now

    @contextlib.contextmanager
    def _locked(self):
        with self.lock:
            path = self.root/'continuation.lock'
            require(not path.is_symlink(), 'owner lock symlink unsupported')
            with path.open('a') as stream:
                fcntl.flock(stream, fcntl.LOCK_EX)
                try: yield
                finally: fcntl.flock(stream, fcntl.LOCK_UN)

    @contextlib.contextmanager
    def _tx(self):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            yield
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK');raise

    def _identity(self):
        require(self.path.is_file() and not self.path.is_symlink() and not self.root.is_symlink(), 'continuation ledger missing or replaced')
        row = self.db.execute('SELECT * FROM meta WHERE id=1').fetchone()
        require(row and row['policy'] == encoded(self.policy).decode() and row['policy_sha256'] == self.policy_sha256
                and sha(encoded(self.policy)) == self.policy_sha256
                and load(regular(self.identity, 4096)) == {'schema_version': 1, 'policy_sha256': self.policy_sha256},
                'immutable continuation identity changed')

    def _row(self):
        self._identity()
        return dict(self.db.execute('SELECT * FROM meta WHERE id=1').fetchone())

    def _event(self, kind, body):
        self.db.execute('INSERT INTO events(at,kind,body) VALUES(?,?,?)', (self._now(), kind, encoded(body).decode()))

    def _version(self, item):
        return dict(id='version-'+sha(encoded(item))[:24], **item)

    def _core_read(self, query, args=()):
        path = Path(self.policy['core_state'])/'tasks.sqlite'
        require(path.is_file() and not path.is_symlink(), 'initialized Core history required')
        db = sqlite3.connect('file:'+str(path)+'?mode=ro', uri=True)
        db.row_factory = sqlite3.Row
        try: return [dict(row) for row in db.execute(query, args)]
        finally: db.close()

    def _core_policy(self):
        descriptor = self.policy['core_policy'];raw = regular(descriptor['path'])
        require(sha(raw) == descriptor['sha256'], 'frozen Core policy SHA changed')
        policy = load(raw)
        require(isinstance(policy, dict) and policy.get('repo') == self.policy['repo'] and policy.get('sources') == ['local']
                and policy.get('permissions') == {'execute_tests': True, 'publish': False, 'network': False}
                and all(type(x) is bool for x in policy['permissions'].values())
                and policy.get('acceptance') == {'require_tests': True, 'require_unchanged_candidate': True}
                and all(type(x) is bool for x in policy['acceptance'].values())
                and (isinstance(policy.get('producer_policy'), dict) or isinstance(policy.get('quality_policy'), dict)),
                'existing local Core producer or quality authority required')
        rows = self._core_read('SELECT body FROM policy WHERE id=1')
        require(len(rows) == 1 and load(rows[0]['body']) == policy, 'Core policy identity mismatch')
        if 'quality_policy' in policy:
            from .quality import load as quality_policy
            quality_policy(policy)
        return policy

    def _runtime(self, version):
        path = absolute(version['controller_path']);root = absolute(self.policy['runtime_root'])
        require(path != root and path.is_relative_to(root) and digest(path/'agent_workflow') == version['package_sha256'], 'selected physical runtime integrity drift')
        return path

    def _git(self, *args):
        result = subprocess.run(['git', '-C', self.policy['repo'], *args], capture_output=True, text=True, timeout=10,
                                env={'PATH': os.defpath+':/opt/homebrew/bin:/usr/local/bin', 'LANG': 'en_US.UTF-8'})
        require(result.returncode == 0, 'Git owner checkpoint observation failed')
        return result.stdout.strip()

    def _core(self, action, args, version):
        path = self._runtime(version)
        argv = [sys.executable, '-B', '-m', 'agent_workflow', '--state', self.policy['core_state'], action, '--args', json.dumps(args)]
        env = {'PATH': os.defpath+':/opt/homebrew/bin:/usr/local/bin', 'PYTHONPATH': str(path), 'PYTHONDONTWRITEBYTECODE': '1', 'LANG': 'en_US.UTF-8'}
        result = subprocess.run(argv, cwd=self.root, env=env, capture_output=True, text=True,
                                timeout=min(30, max(.001, self._row()['deadline']-self._now())))
        body = load(result.stdout)
        require(result.returncode == 0 and body.get('ok') is True, 'selected physical Core operation failed')
        self._runtime(version);self._core_policy()
        return body['result']

    def _status(self, row):
        return {**{key: row[key] for key in ('phase', 'reason', 'next_due', 'started', 'deadline', 'requests_spent', 'stop_reason')},
                'stopped': bool(row['stopped']), 'request': load(row['request']) if row['request'] else None,
                'active_version': load(row['active_version']), 'autonomous_dispatch': False,
                'requires_conversation_bridge': True, 'external_provider_calls': 0}

    def status(self):
        with self._locked(): return self._status(self._row())

    def _phase(self, phase, reason, *, next_due=None):
        with self._tx():
            self.db.execute('UPDATE meta SET phase=?,reason=? WHERE id=1', (phase, reason))
            if next_due is not None: self.db.execute('UPDATE meta SET next_due=? WHERE id=1', (next_due,))
            self._event(phase, {'reason': reason})
        return self._status(self._row())

    def _gate(self, row):
        if row['stopped']: return 'stopped', row['stop_reason']
        if self._now() >= row['deadline']: return 'blocked', 'owner wall deadline exhausted; pending intent retained'
        return None

    def _packet(self, kind, task_id, run_id, version, source):
        return {'id': 'request-'+uuid.uuid4().hex[:24], 'kind': kind, 'task_id': task_id, 'run_id': run_id,
                'version': version['id'], 'controller_path': version['controller_path'],
                'package_sha256': version['package_sha256'], 'source_sha256': source}

    def _quality_projection(self, version):
        anchors = self._core_read('SELECT id FROM tasks ORDER BY rowid DESC LIMIT 1')
        require(anchors, 'existing Core observation anchor required')
        projection = self._core('next_step', {'task_id': anchors[0]['id']}, version)['quality']
        require(projection['source_sha256'] == digest(Path(self.policy['repo']))
                and projection['policy_sha256'] == self._core_policy()['quality_policy']['sha256'],
                'quality projection identity changed')
        return projection

    def _quality_identity(self, pending, version):
        require(pending['source_sha256'] == digest(Path(self.policy['repo']))
                and pending['core_policy_sha256'] == self.policy['core_policy']['sha256']
                and pending['quality_policy_sha256'] == self._core_policy()['quality_policy']['sha256']
                and pending['version'] == version['id']
                and pending['controller_path'] == version['controller_path']
                and pending['package_sha256'] == version['package_sha256'],
                'pending semantic review identity drift')

    @staticmethod
    def _goal_wait(projection):
        return (projection.get('scope') == 'owner_goals'
                and projection.get('assessment_sha256') is not None
                and projection['reason'] in {'goal_planning_required', 'goal_research_required',
                    'goal_validation_required', 'goal_capability_required',
                    'goal_approval_required', 'goal_task_unavailable'})

    def _resolved_quality(self, version, projection, request=None):
        # Only actual successful journal entries under the current validated
        # identities can acknowledge a review. No caller-provided completion flag.
        records = self.db.execute("SELECT body FROM events WHERE kind='quality_review_resolved' ORDER BY seq DESC LIMIT ?",
                                  (self.policy['budget']['max_requests'],)).fetchall()
        for record in records:
            event = load(record[0]); prior = event['request']
            if request is not None and prior != request: continue
            if event['projection'] != projection: continue
            try: self._quality_identity(prior, version)
            except ValueError: continue
            return True
        return False

    def _quality_tick(self, row, version):
        projection = self._quality_projection(version)
        gate = self._gate(self._row())
        if gate: return self._phase(*gate)
        if self._goal_wait(projection) and self._resolved_quality(version, projection):
            return self._phase('blocked', 'quality '+projection['disposition']+': '+projection['reason'])
        if projection['claim_allowed']:
            if row['requests_spent'] + 2 > self.policy['budget']['max_requests']:
                return self._phase('blocked', 'budget: owner request ceiling cannot reserve worker and review')
            packet = self._packet('worker', projection['next_task_id'], None, version, projection['source_sha256'])
            cost, phase = 2, 'worker_requested'
        elif projection['disposition'] == 'wait_review' and projection['reason'] != 'verification_running':
            if row['requests_spent'] + 1 > self.policy['budget']['max_requests']:
                return self._phase('blocked', 'budget: owner semantic request ceiling exhausted')
            packet = self._packet('quality_review', None, None, version, projection['source_sha256'])
            packet.update(core_policy_sha256=self.policy['core_policy']['sha256'],
                          quality_policy_sha256=projection['policy_sha256'],
                          projection_request_id=projection['request_id'])
            cost, phase = 1, 'quality_review_requested'
        elif projection['disposition'] == 'stop_no_value':
            with self._tx(): self.db.execute('UPDATE meta SET failure_count=0 WHERE id=1')
            return self._phase('stop_no_value', 'current independent projection found no novel value',
                               next_due=self._now()+self.policy['cadence']['interval_seconds'])
        else:
            return self._phase('blocked', 'quality '+projection['disposition']+': '+projection['reason'])
        with self._tx():
            self.db.execute('UPDATE meta SET requests_spent=requests_spent+?,request=?,failure_count=0 WHERE id=1',
                            (cost, encoded(packet).decode()))
            self._event('quality_review_reserved' if cost == 1 else 'worker_pair_reserved', packet)
        return self._phase(phase, 'reserved owner-bound quality intent')

    def resolve_quality_review(self, request_id):
        with self._locked():
            row = self._row();require(self._gate(row) is None, 'owner stopped or expired')
            version = load(row['active_version']);self._runtime(version);self._core_policy()
            pending = load(row['request']) if row['request'] else None
            require(pending and pending['id'] == request_id and pending['kind'] == 'quality_review',
                    'pending semantic review required')
            self._quality_identity(pending, version)
            projection = self._quality_projection(version)
            require(projection['assessment_sha256'] is not None
                    and (projection['disposition'] not in {'wait_review', 'wait_owner'}
                         or self._goal_wait(projection)),
                    'fresh independently assessed quality projection required')
            require(self._gate(self._row()) is None, 'owner stopped or expired')
            with self._tx():
                self.db.execute('UPDATE meta SET request=NULL,phase=?,reason=?,failure_count=0 WHERE id=1',
                                ('ready', 'fresh independent semantic review resolved'))
                self._event('quality_review_resolved', {'request': pending, 'projection': projection})
            return self._status(self._row())

    def tick(self):
        with self._locked():
            row = self._row();gate = self._gate(row)
            if gate: return self._phase(*gate)
            try:
                version = load(row['active_version']);self._runtime(version);policy = self._core_policy()
                pending = load(row['request']) if row['request'] else None
                if pending:
                    require(pending['version'] == version['id'] and pending['package_sha256'] == version['package_sha256']
                            and pending['controller_path'] == version['controller_path'], 'pending request version identity changed')
                    if row['failure_count'] and self._now() < row['next_due']:
                        return self._phase('not_due', 'durable pending Core backoff due time not reached')
                    if pending['kind'] == 'quality_review':
                        self._quality_identity(pending, version)
                        return self._phase('quality_review_requested', 'pending independent semantic review; resolve before dispatch')
                    if pending['run_id'] is None: return self._phase('worker_requested', 'pending conversation worker request; reconcile before dispatch')
                    run = self._core('status', {'run_id': pending['run_id']}, version)
                    require(run['task_id'] == pending['task_id'] and run['workflow'] == pending['package_sha256']
                            and run['baseline'] == pending['source_sha256'], 'bound Core run identity changed')
                    if run['state'] in {'ready_review', 'accepted'}:
                        if pending['kind'] == 'worker':
                            review = self._packet('review', pending['task_id'], run['id'], version, pending['source_sha256'])
                            with self._tx():
                                self.db.execute('UPDATE meta SET request=? WHERE id=1', (encoded(review).decode(),))
                                self._event('review_requested', review)
                        if run['state'] == 'accepted':
                            return self._phase('awaiting_promotion', 'accepted Core evidence requires independent receipt and version CAS')
                        return self._phase('review_requested', 'actual Core run requires independent conversation review')
                    if run['state'] in {'running', 'changed', 'verifying', 'verified'} and time.time() < min(run['lease_until'], run['deadline']):
                        return self._phase('in_flight', 'bound Core lease remains active')
                    return self._phase('blocked', 'bound Core attempt terminal or expired; bridge reconciliation required; reservation remains spent')
                if 'quality_policy' not in policy and row['requests_spent'] + 2 > self.policy['budget']['max_requests']:
                    return self._phase('blocked', 'owner request ceiling cannot reserve worker and independent review')
                if self._now() < row['next_due']: return self._phase('not_due', 'durable discovery due time not reached')
                active = self._core_read("SELECT id FROM runs WHERE state IN ('running','changed','verifying','verified','ready_review') AND lease_until>? AND deadline>?", (time.time(), time.time()))
                if active: return self._phase('blocked', 'existing Core checkout lease requires bridge reconciliation')
                if 'quality_policy' in policy:
                    unbound_reviews = self._core_read("SELECT id FROM runs WHERE state IN ('verified','ready_review')")
                    if unbound_reviews:
                        return self._phase('blocked', 'unbound Core verification requires bridge reconciliation')
                    return self._quality_tick(row, version)
                current = digest(Path(self.policy['repo']))
                discovery = self._core('producer_discover', {}, version)
                require(digest(Path(self.policy['repo'])) == current, 'source changed during discovery')
                gate = self._gate(self._row())
                if gate: return self._phase(*gate)
                # Core admission may have completed before a bridge crash. Reconcile
                # its durable finding rows, never the position of arbitrary tasks.
                ids = [item['task_id'] for item in self._core_read(
                    "SELECT DISTINCT task_id FROM producer_candidates WHERE status IN ('admitted','deduplicated') AND task_id IS NOT NULL ORDER BY created,id")]
                eligible = None
                for task_id in ids:
                    findings = self._core_read('SELECT c.payload,c.evidence,t.state,b.body FROM producer_candidates c JOIN tasks t ON t.id=c.task_id JOIN producer_task_checks b ON b.task_id=t.id WHERE t.id=?', (task_id,))
                    for item in findings:
                        evidence = load(item['evidence']);report = evidence.get('report', {});result = evidence.get('result', {});binding = load(item['body'])
                        count = self._core_read('SELECT COUNT(*) AS n FROM runs WHERE task_id=?', (task_id,))[0]['n']
                        if (item['state'] == 'queued' and count < policy['budget']['max_attempts']
                                and evidence.get('source') == current and report.get('exactsourceSHA') == current
                                and evidence.get('provenance') == 'machine_reproduced' and result.get('exit_code') == 1
                                and report.get('passed') is False and report.get('classification') == 'product_defect'
                                and report.get('source_stable') is True and report.get('id') == binding['check_id']
                                and binding['policy_sha256'] == policy['producer_policy']['sha256']):
                            eligible = task_id;break
                    if eligible: break
                if eligible is None:
                    with self._tx():
                        self.db.execute('UPDATE meta SET failure_count=0 WHERE id=1')
                        self._event('discovery', {'source_sha256': current, 'version': version['id'], 'result': discovery})
                    return self._phase('idle', 'no current machine-reproduced eligible owner finding; retry when due', next_due=self._now()+self.policy['cadence']['interval_seconds'])
                packet = self._packet('worker', eligible, None, version, current)
                with self._tx():
                    self.db.execute('UPDATE meta SET requests_spent=requests_spent+2,request=?,failure_count=0 WHERE id=1', (encoded(packet).decode(),))
                    self._event('worker_pair_reserved', packet)
                return self._phase('worker_requested', 'reserved conversation worker and independent review request pair')
            except subprocess.TimeoutExpired:
                failures = self._row()['failure_count']+1
                wait = min(self.policy['cadence']['max_backoff_seconds'], self.policy['cadence']['backoff_seconds']*2**min(failures-1, 20))
                with self._tx(): self.db.execute('UPDATE meta SET failure_count=? WHERE id=1', (failures,))
                return self._phase('backoff', 'physical Core command timed out; reconcile before retry', next_due=self._now()+wait)
            except (ContinuationError, ValueError, OSError, sqlite3.DatabaseError, KeyError, TypeError) as error:
                return self._phase('blocked', 'integrity: '+type(error).__name__+'; owner diagnosis required')

    def bind_run(self, request_id, run_id):
        with self._locked():
            row = self._row();require(self._gate(row) is None, 'owner stopped or expired')
            version = load(row['active_version']);self._core_policy();self._runtime(version)
            pending = load(row['request']) if row['request'] else None
            require(pending and pending['id'] == request_id and pending['kind'] == 'worker', 'pending worker request required')
            require(pending['run_id'] in (None, run_id), 'request already bound to another run')
            run = self._core('status', {'run_id': run_id}, version)
            require(run['id'] == run_id and run['task_id'] == pending['task_id']
                    and run['state'] in {'running', 'changed', 'verifying', 'verified', 'ready_review'}
                    and run['baseline'] == pending['source_sha256']
                    and pending['version'] == version['id'] and pending['controller_path'] == version['controller_path']
                    and run['workflow'] == pending['package_sha256'] == version['package_sha256']
                    and time.time() < min(run['lease_until'], run['deadline']), 'actual version-bound active Core lease required')
            if run['state'] in {'verified', 'ready_review'}:
                # The immutable claim baseline precedes the worker's actual change.
                # Reuse physical Core proof validation instead of rebasing the intent.
                proof = self._core('next_step', {'run_id': run_id}, version)
                require(proof['run_id'] == run_id and proof['task_id'] == run['task_id']
                        and proof['stored_state'] == run['state']
                        and proof['source_match'] is True and proof['evidence_valid'] is True
                        and run['candidate'] == digest(Path(self.policy['repo'])),
                        'current verified Core candidate proof required for late binding')
            else:
                require(pending['source_sha256'] == digest(Path(self.policy['repo'])),
                        'unverified binding requires unchanged request source')
            require(self._gate(self._row()) is None and time.time() < min(run['lease_until'], run['deadline']),
                    'original owner deadline and Core lease must remain live after observation')
            pending['run_id'] = run_id
            with self._tx():
                self.db.execute('UPDATE meta SET request=? WHERE id=1', (encoded(pending).decode(),))
                self._event('run_bound', pending)
            return self._status(self._row())

    def stop(self, reason):
        require(isinstance(reason, str) and bool(reason.strip()) and len(reason.encode()) <= 4096, 'explicit bounded stop reason required')
        with self._locked():
            self._row()
            with self._tx():
                self.db.execute('UPDATE meta SET stopped=1,stop_reason=?,phase=?,reason=? WHERE id=1', (reason, 'stopped', reason))
                self._event('owner_stop', {'reason': reason})
            return self._status(self._row())

    def resume(self):
        with self._locked():
            row = self._row()
            with self._tx(): self._event('resume_observed', {'stopped': bool(row['stopped']), 'stop_reason': row['stop_reason']})
            return self._status(self._row())

    def clear_stop(self, reason):
        require(isinstance(reason, str) and bool(reason.strip()) and len(reason.encode()) <= 4096, 'explicit bounded owner clear reason required')
        with self._locked():
            self._row()
            with self._tx():
                self.db.execute('UPDATE meta SET stopped=0,stop_reason=NULL,phase=?,reason=? WHERE id=1', ('ready', reason))
                self._event('owner_stop_cleared', {'reason': reason})
            return self._status(self._row())

    def promote(self, request_id, receipt_path, receipt_sha256, controller_path, expected_version):
        with self._locked():
            row = self._row();require(self._gate(row) is None, 'owner stopped or expired')
            old = load(row['active_version']);require(old['id'] == expected_version, 'active version CAS conflict')
            self._core_policy();self._runtime(old)
            pending = load(row['request']) if row['request'] else None
            require(pending and pending['id'] == request_id and pending['kind'] == 'review' and pending['version'] == old['id'], 'bound independent review request required')
            run = self._core('status', {'run_id': pending['run_id']}, old)
            task = self._core('status', {'task_id': pending['task_id']}, old)
            require(run['state'] == 'accepted' and task['state'] == 'accepted' and run['task_id'] == pending['task_id']
                    and run['workflow'] == pending['package_sha256'] == old['package_sha256']
                    and run['baseline'] == pending['source_sha256'], 'actual accepted version-bound Core run required')
            evidence = load(run['evidence'])
            require(evidence['candidate'] == run['candidate'] == digest(Path(self.policy['repo']))
                    and evidence['workflow'] == run['workflow'] and evidence['baseline'] == run['baseline']
                    and evidence['source_unchanged'] is True and evidence['ownership_valid_at_finish'] is True
                    and len(evidence['results']) >= len(self._core_policy()['tests'])
                    and all(item['exit_code'] == 0 for item in evidence['results']), 'fresh accepted machine evidence required')
            path = absolute(receipt_path);require(not path.is_relative_to(Path(self.policy['repo'])), 'independent receipt must be outside editable repo')
            raw = regular(path);require(hash_value(receipt_sha256) and sha(raw) == receipt_sha256, 'independent receipt SHA mismatch')
            receipt = load(raw)
            require(isinstance(receipt, dict) and set(receipt) == {'schema_version', 'approved', 'reviewer', 'run_id', 'core_state', 'source_root', 'source_sha256', 'controller_package_sha256'}
                    and type(receipt['schema_version']) is int and receipt['schema_version'] == 1 and receipt['approved'] is True
                    and isinstance(receipt['reviewer'], str) and receipt['reviewer'].strip() and receipt['reviewer'] != run['worker']
                    and receipt['run_id'] == run['id'] and receipt['core_state'] == self.policy['core_state']
                    and receipt['source_root'] == self.policy['repo'] and receipt['source_sha256'] == run['candidate']
                    and receipt['controller_package_sha256'] == run['workflow'], 'exact independently approved receipt required')
            controller = absolute(controller_path)
            require(str(controller) != old['controller_path'], 'new immutable physical runtime required')
            package = digest(controller/'agent_workflow')
            require(package == digest(Path(self.policy['repo'])/'agent_workflow') and not self._git('status', '--porcelain'), 'clean accepted repo and exact physical package required')
            version = self._version({'controller_path': str(controller), 'package_sha256': package,
                                     'source_sha256': run['candidate'], 'head': self._git('rev-parse', 'HEAD')})
            self._runtime(version)
            active = self._core_read("SELECT id FROM runs WHERE state IN ('running','changed','verifying','verified','ready_review') AND lease_until>? AND deadline>?", (time.time(), time.time()))
            require(not active, 'in-flight Core work prevents version handoff')
            with self._tx():
                latest = self._row();require(load(latest['active_version'])['id'] == expected_version and latest['request'] == row['request'], 'promotion CAS conflict')
                self.db.execute('UPDATE meta SET active_version=?,request=NULL,phase=?,reason=?,next_due=? WHERE id=1',
                                (encoded(version).decode(), 'ready', 'independently accepted physical version promoted', self._now()+self.policy['cadence']['interval_seconds']))
                self._event('version_promoted', {'previous': old, 'active': version, 'request': pending,
                                              'receipt_path': str(path), 'receipt_sha256': receipt_sha256, 'core_evidence_sha256': sha(encoded(evidence))})
            return self._status(self._row())


def main():
    parser = argparse.ArgumentParser(description='Durable requests for the authorized conversation bridge; no model dispatch')
    parser.add_argument('--state', required=True);parser.add_argument('--policy')
    parser.add_argument('action', choices=['init', 'tick', 'status', 'stop', 'resume', 'clear_stop', 'bind_run', 'promote', 'resolve_quality_review'])
    parser.add_argument('--args', default='{}');args = parser.parse_args();owner = None
    try:
        policy = load(regular(args.policy)) if args.policy else None
        owner = Continuation(args.state, policy)
        result = owner.status() if args.action == 'init' else getattr(owner, args.action)(**load(args.args))
        print(json.dumps({'ok': True, 'result': result}, sort_keys=True))
    except (ValueError, TypeError, OSError, sqlite3.DatabaseError) as error:
        print(json.dumps({'ok': False, 'error': type(error).__name__+': '+str(error)}));raise SystemExit(1)
    finally:
        if owner is not None and owner.db is not None: owner.db.close()


if __name__ == '__main__': main()
