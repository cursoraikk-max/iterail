"""Foreground UI audit scheduling. No capture, backend, timer service or visual approval."""
import copy
import contextlib
import fcntl
import math
from pathlib import Path
import sqlite3
import threading
import time

from .uiux import UIUXIntegrityError, UIUXStore, canonical, encoded, integrity_boundary, read_regular, source_digest, strict_json, validate_policy, require


class UIUXMaintenance:
    @integrity_boundary()
    def __init__(self, state, policy, source_root, task_sink=None, clock=time.time, gate=lambda: True):
        self.policy = validate_policy(policy)
        self.policy_sha256 = canonical(self.policy)
        self.enabled = self.policy['project']['has_ui']
        self.root = Path(state)
        self.source_root = Path(source_root)
        require(not self.root.is_symlink() and not self.source_root.is_symlink(), 'UI scheduler scope symlink unsupported')
        self.root = self.root.resolve();self.source_root = self.source_root.resolve()
        require(not self.root.is_relative_to(self.source_root) or self.root.is_relative_to(self.source_root/'.workflow'), 'UI scheduler runtime must remain outside source or in .workflow')
        self.clock, self.gate, self.task_sink = clock, gate, task_sink
        self.db = None
        self.lock = threading.RLock()
        self.path = self.root/'uiux-maintenance.sqlite'
        self.identity_path = self.root/'uiux-maintenance.identity.json'
        self.binding = {'schema_version': 1, 'policy_sha256': self.policy_sha256, 'source_root': str(self.source_root)}
        if self.enabled:
            with self._locked(): self._read()

    @contextlib.contextmanager
    def _locked(self):
        with self.lock:
            self.root.mkdir(parents=True, exist_ok=True)
            path = self.root/'uiux-maintenance.lock'
            require(not path.is_symlink(), 'UI scheduler lock symlink unsupported')
            with path.open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                try: yield
                finally: fcntl.flock(lock, fcntl.LOCK_UN)

    def _initial(self):
        return {'policy_sha256': self.policy_sha256, 'source_root': str(self.source_root), 'next_due': 0.0,
                'failure_count': 0, 'stopped': False, 'last_run_id': None, 'last_checked': None, 'pending': None, 'last_error': None}

    @integrity_boundary()
    def _check(self, row):
        require(row['policy_sha256'] == self.policy_sha256 and row['source_root'] == str(self.source_root)
                and canonical(self.policy) == self.policy_sha256, 'UI scheduler owner policy or source scope changed')
        require(strict_json(read_regular(self.identity_path, 16384)) == self.binding, 'UI scheduler binding history changed')
        value = dict(row);value['stopped'] = bool(value['stopped'])
        return value

    @integrity_boundary()
    def _read(self):
        if not self.path.exists():
            require(not self.identity_path.exists(), 'initialized UI scheduler history missing')
            return self._initial()
        require(self.path.is_file() and not self.path.is_symlink() and not self.root.is_symlink(), 'regular UI scheduler ledger required')
        d = sqlite3.connect('file:'+str(self.path)+'?mode=ro', uri=True)
        d.row_factory = sqlite3.Row
        try:
            row = d.execute('SELECT * FROM meta WHERE id=1').fetchone()
            require(row is not None, 'initialized UI scheduler metadata missing')
            return self._check(row)
        finally: d.close()

    @integrity_boundary()
    def _open(self):
        self._read()
        if self.db is not None: return
        existed = self.path.exists()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, isolation_level=None, timeout=10, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        if not existed:
            self.db.execute('CREATE TABLE meta(id INTEGER PRIMARY KEY,policy_sha256 TEXT,source_root TEXT,next_due REAL,failure_count INTEGER,stopped INTEGER,last_run_id TEXT,last_checked REAL,pending TEXT,last_error TEXT)')
            self.db.execute('CREATE TABLE stale_intents(external_key TEXT PRIMARY KEY,proposal TEXT,reason TEXT,at REAL)')
            self.db.execute('INSERT INTO meta VALUES(1,?,?,0,0,0,NULL,NULL,NULL,NULL)', (self.policy_sha256, str(self.source_root)))
            self.identity_path.write_bytes(encoded(self.binding))

    @integrity_boundary()
    def _scope(self):
        # Compare persisted paths before any source hash, including foreign standalone Store rows.
        row = self._read()
        path = self.root/'uiux/uiux.sqlite'
        require((row['last_run_id'] is None and row['pending'] is None) or path.exists(),
                'initialized UI Store history missing')
        if path.exists():
            require(path.is_file() and not path.is_symlink(), 'regular UI Store required')
            d = sqlite3.connect('file:'+str(path)+'?mode=ro', uri=True)
            try:
                require(all(x[0] == str(self.source_root) for x in d.execute('SELECT source_root FROM runs')), 'UI Store bound to another source')
            finally: d.close()

    @integrity_boundary()
    def _pending(self, row, store):
        if row['pending'] is None: return None
        pending = strict_json(row['pending'])
        require(isinstance(pending, dict) and set(pending) == {'run_id', 'source_sha256', 'proposal'},
                'exact UI pending intent required')
        require(isinstance(pending['run_id'], str) and pending['run_id'] == row['last_run_id'],
                'UI pending run identity changed')
        run = store._run(pending['run_id'])
        require(run['source_root'] == str(self.source_root) and run['source_sha256'] == pending['source_sha256'],
                'UI pending intent scope changed')
        require(pending['proposal'] == store._discovery_result('review_required', run['id'], run['source_sha256'])['audit_proposal'],
                'UI pending proposal identity changed')
        return pending

    def status(self):
        with self.lock:
            value = self._initial() if not self.enabled else self._read()
            value.pop('pending', None)
            stale = []
            if self.enabled and self.path.exists():
                d = sqlite3.connect('file:'+str(self.path)+'?mode=ro', uri=True)
                try:
                    stale = [{'proposal': strict_json(x[0]), 'reason': x[1]} for x in d.execute('SELECT proposal,reason FROM stale_intents ORDER BY rowid')]
                finally: d.close()
            return dict(value, stale_intents=stale, phase='not_applicable' if not self.enabled else 'scheduled', production_visual_verified=False)

    def _result(self, phase, row, **extra):
        return dict(phase=phase, run_id=row['last_run_id'], next_due=row['next_due'], failure_count=row['failure_count'],
                    stopped=bool(row['stopped']), policy_sha256=self.policy_sha256, production_visual_verified=False, **extra)

    def _observe_stop(self, row, stopped):
        if row['stopped'] != stopped:
            self.db.execute('UPDATE meta SET stopped=? WHERE id=1', (int(stopped),))
            return self._read()
        return row

    def tick(self, stopped=False):
        require(type(stopped) is bool, 'authoritative boolean stop required')
        if not self.enabled: return self._result('not_applicable', self._initial())
        with self._locked():
            self._open()
            row = self._read()
            if stopped or not self.gate(): return self._result('paused', self._observe_stop(row, stopped))
            now = self.clock()
            require(type(now) in (int, float) and math.isfinite(now), 'finite UI scheduler clock required')
            if now < row['next_due']: return self._result('not_due', self._observe_stop(row, stopped))
            store = None
            try:
                self._scope()
                store = UIUXStore(self.root/'uiux', self.policy, clock=self.clock)
                pending = self._pending(row, store)
                with integrity_boundary(): current = source_digest(self.source_root)
                row = self._observe_stop(row, stopped)
                if pending and pending['source_sha256'] != current:
                    # A stale audit may already exist in Core; never export it again for changed source.
                    self.db.execute('BEGIN IMMEDIATE')
                    try:
                        self.db.execute('INSERT OR IGNORE INTO stale_intents VALUES(?,?,?,?)',
                                        (pending['proposal']['external_key'], encoded(pending['proposal']).decode(), 'source_changed', now))
                        self.db.execute('UPDATE meta SET pending=NULL WHERE id=1')
                        self.db.execute('COMMIT')
                    except BaseException:
                        if self.db.in_transaction: self.db.execute('ROLLBACK')
                        raise
                    pending = None
                if pending is None:
                    result = store.discover(self.source_root)
                    proposal = result['audit_proposal']
                    pending = {'run_id': result['run_id'], 'source_sha256': result['source_sha256'], 'proposal': proposal}
                    self.db.execute('UPDATE meta SET pending=?,last_run_id=? WHERE id=1', (encoded(pending).decode(), pending['run_id']))
                # Stop/deadline admission is live, even if it changed during discovery.
                if not self.gate(): return self._result('paused', self._read())
                store._fresh(store._run(pending['run_id']))
                if not self.gate(): return self._result('paused', self._read())
                if self.task_sink is not None: self.task_sink(copy.deepcopy(pending['proposal']))
                if not self.gate(): return self._result('paused', self._read())
                self.db.execute('UPDATE meta SET pending=NULL,failure_count=0,last_error=NULL,last_checked=?,next_due=? WHERE id=1', (now, now+self.policy['cadence']['interval_seconds']))
                return self._result('due', self._read(), source_sha256=pending['source_sha256'])
            except UIUXIntegrityError as error:
                return self._result('blocked', row, failure_class='integrity', error=type(error).__name__)
            except Exception as error:
                if not self.gate(): return self._result('paused', self._read())
                failures = self._read()['failure_count'] + 1
                wait = min(self.policy['cadence']['interval_seconds'], 30*2**min(failures-1, 10))
                self.db.execute('UPDATE meta SET failure_count=?,last_error=?,last_checked=?,next_due=? WHERE id=1', (failures, type(error).__name__, now, now+wait))
                return self._result('backoff', self._read(), error=type(error).__name__)
            finally:
                if store is not None: store.db.close()
