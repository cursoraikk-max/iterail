"""Explicit bounded multi-intent host pull; no provider, wake source or acceptance.

A separate owner grant opts in. Existing OneShot keeps its one-intent ceiling.
Every invocation performs at most one new dispatch, using existing Core gates.
"""
import argparse
import contextlib
import fcntl
import json
import math
from pathlib import Path
import sqlite3
import time
import uuid
from .continuation import Continuation, absolute, digest, encoded, load, regular, require, sha
from .hostbridge import HostBridge
from .hostsupervisor import HostAdapter, LocalProcess


class Flow:
    def __init__(self, continuation, adapter: HostAdapter, policy_path, policy_sha256):
        self.c = continuation
        self.adapter = adapter
        self.path = absolute(policy_path)
        require(not self.path.is_relative_to(Path(self.c.policy['repo'])), 'external flow owner policy required')
        raw = regular(self.path)
        require(sha(raw) == policy_sha256, 'pinned flow policy required')
        p = load(raw)
        require(isinstance(p, dict) and set(p) == {'schema_version', 'continuation_state',
                'continuation_identity_sha256', 'adapter_identity', 'allowed_roles', 'max_intents', 'poll_seconds'}
                and type(p['schema_version']) is int and p['schema_version'] == 1
                and p['continuation_state'] == str(self.c.root)
                and p['continuation_identity_sha256'] == sha(regular(self.c.root/'identity.json'))
                and isinstance(p['adapter_identity'], str) and 0 < len(p['adapter_identity']) <= 1024
                and p['adapter_identity'] == adapter.identity, 'exact flow identity required')
        require(isinstance(p['allowed_roles'], list) and p['allowed_roles']
                and all(role in {'worker', 'review'} for role in p['allowed_roles'])
                and len(set(p['allowed_roles'])) == len(p['allowed_roles'])
                and type(p['max_intents']) is int and 1 <= p['max_intents'] <= self.c.policy['budget']['max_requests']
                and type(p['poll_seconds']) is int and 1 <= p['poll_seconds'] <= 60,
                'explicit bounded flow roles and resources required')
        self.policy = p
        self.policy_sha = policy_sha256
        with self.c._locked():
            self._controller()
            self.c.db.execute('CREATE TABLE IF NOT EXISTS host_process_intents(request_id TEXT PRIMARY KEY, body TEXT NOT NULL)')
            self.c.db.execute('CREATE TABLE IF NOT EXISTS host_adapter_identity(id INTEGER PRIMARY KEY CHECK(id=1), identity TEXT NOT NULL)')
            self.c.db.execute('CREATE TABLE IF NOT EXISTS host_flow_grant(id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL, next_due REAL NOT NULL)')
            row = self.c._row()
            grant = {'policy': p, 'policy_sha256': policy_sha256, 'started': row['started'], 'deadline': row['deadline']}
            with self.c._tx():
                old = self.c.db.execute('SELECT body FROM host_flow_grant WHERE id=1').fetchone()
                if old:
                    require(load(old[0]) == grant, 'immutable original flow grant required')
                else:
                    require(not self.c.db.execute('SELECT 1 FROM host_process_intents').fetchone(),
                            'cannot silently adopt one-shot intent into flow')
                    self.c.db.execute('INSERT INTO host_flow_grant VALUES(1,?,0)', (encoded(grant).decode(),))
                    self.c._event('host_flow_authorized', grant)
                identity = self.c.db.execute('SELECT identity FROM host_adapter_identity WHERE id=1').fetchone()
                if identity:
                    require(identity[0] == adapter.identity, 'immutable host adapter identity changed')
                else:
                    self.c.db.execute('INSERT INTO host_adapter_identity VALUES(1,?)', (adapter.identity,))
        self.bridge = HostBridge(self.c)

    def _controller(self):
        version = load(self.c._row()['active_version'])
        self.c._runtime(version)
        require(digest(Path(__file__).resolve().parent) == version['package_sha256'], 'reviewed host controller required')
        require(sha(regular(self.path)) == self.policy_sha, 'flow policy changed')

    @contextlib.contextmanager
    def _serial(self):
        # Same lock as OneShot. A competing wake exits; it never waits or starts.
        path = self.c.root/'host-oneshot.lock'
        require(not path.is_symlink(), 'regular host serialization lock required')
        with path.open('a') as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError('host flow busy; no dispatch') from None
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)

    def _intents(self):
        with self.c._locked():
            self.c._identity()
            rows = self.c.db.execute('SELECT body FROM host_process_intents ORDER BY rowid LIMIT ?',
                                     (self.policy['max_intents']+1,)).fetchall()
            require(len(rows) <= self.policy['max_intents'], 'flow intent ceiling inconsistent')
            values = [load(row[0]) for row in rows]
            require(all(x.get('flow_policy_sha256') == self.policy_sha for x in values), 'exact flow history required')
            return values

    def _record(self, intent, value):
        require(isinstance(value, dict) and set(value) == {'execution_id', 'dispatch_key', 'state', 'sequence', 'observed_at'}
                and value['execution_id'] == intent['execution_id'] and value['dispatch_key'] == intent['dispatch_key']
                and value['state'] in {'uncertain', 'running', 'completed', 'cancelled', 'expired', 'failed'}
                and type(value['sequence']) is int and value['sequence'] >= 0
                and type(value['observed_at']) in (int, float) and math.isfinite(value['observed_at'])
                and value['observed_at'] <= time.time()+1, 'structured exact host observation required')
        with self.c._locked():
            records = self.c.db.execute("SELECT body FROM events WHERE kind='host_flow_observed' ORDER BY seq DESC").fetchall()
            previous = [load(row[0]) for row in records if load(row[0])['request_id'] == intent['request_id']]
            if value['state'] != 'uncertain':
                require(value['sequence'] > 0, 'positive host cursor required')
                if value['state'] == 'running':
                    require(time.time()-value['observed_at'] <= 2, 'stale running observation')
                for prior in previous:
                    old = prior['host']
                    require(value['sequence'] >= old['sequence'], 'host cursor regressed')
                    if old['state'] in {'completed', 'cancelled', 'expired', 'failed'}:
                        require(value == old, 'terminal host observation changed')
            body = {'request_id': intent['request_id'], 'host': value}
            if not previous or previous[0] != body:
                with self.c._tx():
                    self.c._event('host_flow_observed', body)
        return value

    def _result(self, phase, *, intent=None, host=None, detail=None, defer=True):
        with self.c._locked():
            row = self.c._row()
            due = min(row['deadline'], self.c._now()+self.policy['poll_seconds'])
            if defer:
                with self.c._tx():
                    self.c.db.execute('UPDATE host_flow_grant SET next_due=? WHERE id=1', (due,))
            else:
                due = self.c.db.execute('SELECT next_due FROM host_flow_grant WHERE id=1').fetchone()[0]
            result = {'phase': phase, 'next_due': due, 'continuation': self.c._status(row),
                      'core_acceptance': False, 'new_dispatches': 0}
        if intent:
            result['intent'] = {k: v for k, v in intent.items() if k != 'nonce'}
        if host is not None:
            result['host'] = host
        if detail is not None:
            result['detail'] = detail
        return result

    def run(self, action='tick'):
        require(action in {'tick', 'observe', 'reconcile', 'cancel'}, 'flow action required')
        # Stop is durable before contending with another caller or slow adapter.
        if action == 'cancel':
            self.c.stop('host flow cancellation requested')
        with self._serial():
            with self.c._locked():
                self._controller()
                row = self.c._row()
                gate = self.c._gate(row)
                due = self.c.db.execute('SELECT next_due FROM host_flow_grant WHERE id=1').fetchone()[0]
                early = not gate and action == 'tick' and self.c._now() < due
            if early:
                return self._result('not_due', defer=False)
            history = self._intents()
            if history:
                intent = history[-1]
                operation = self.adapter.cancel if gate else self.adapter.observe if action == 'observe' else self.adapter.reconcile
                host = self._record(intent, operation(intent))
                if gate:
                    return self._result('stopped', intent=intent, host=host, detail=gate)
                if action == 'observe' or host['state'] != 'completed':
                    return self._result('host_'+host['state'], intent=intent, host=host)
                # Completed process evidence is never machine proof or acceptance.
                bridge = self.bridge.reconcile(intent['request_id'])
                if bridge['phase'] not in {'bound', 'completed'}:
                    return self._result('awaiting_core_or_receipt', intent=intent, host=host, detail=bridge)
            elif gate:
                return self._result('stopped', detail=gate)
            if action != 'tick':
                return self._result('reconciled' if history else 'not_started')
            if len(history) >= self.policy['max_intents']:
                return self._result('intent_ceiling')
            status = self.c.tick()
            packet = status['request']
            if (status['phase'] not in {'worker_requested', 'review_requested'} or packet is None
                    or history and packet['id'] == history[-1]['request_id']):
                return self._result('awaiting_core_or_due', detail=status['phase'])
            if packet['kind'] not in self.policy['allowed_roles']:
                return self._result('role_not_authorized', detail=packet['kind'])
            ticket = self.bridge.reserve()
            require(ticket['request_id'] == packet['id'] and ticket['phase'] == 'reserved'
                    and ticket['dispatch_started'] is False, 'consumed dispatch requires exact reconciliation; never redispatch')
            begun = self.bridge.begin(ticket['request_id'])
            with self.c._locked():
                self._controller()
                row = self.c._row()
                require(self.c._gate(row) is None, 'original continuation stopped or expired')
                self.bridge._pending(self.bridge._get(ticket['request_id']), row)
                intent = {'request_id': ticket['request_id'], 'dispatch_key': ticket['dispatch_key'],
                          'execution_id': 'execution-'+uuid.uuid4().hex, 'nonce': uuid.uuid4().hex,
                          'adapter_identity': self.adapter.identity, 'role': ticket['kind'], 'deadline': row['deadline'],
                          'workdir': self.adapter.workdir, 'source_root': self.c.policy['repo'],
                          'continuation_state': str(self.c.root), 'packet': begun['packet'],
                          'flow_policy_sha256': self.policy_sha}
                with self.c._tx():
                    self.c.db.execute('INSERT INTO host_process_intents VALUES(?,?)', (intent['request_id'], encoded(intent).decode()))
                    self.c._event('host_flow_intent', {k: v for k, v in intent.items() if k not in {'nonce', 'packet'}})
                require(self.c._gate(self.c._row()) is None, 'original continuation stopped or expired')
            # Do not hold the owner ledger lock across an external adapter call:
            # cancel must be able to persist stop even if start is slow. The
            # adapter still owns its final stop/deadline check and spawn race.
            observation = self.adapter.start(intent)
            host = self._record(intent, observation)
            result = self._result('dispatched', intent=intent, host=host)
            result['new_dispatches'] = 1
            return result


def main():
    parser = argparse.ArgumentParser(description='Explicit bounded host flow; local fixture CLI, no models or OS service')
    for name in ('state', 'flow-policy', 'flow-policy-sha256', 'host-policy', 'host-policy-sha256'):
        parser.add_argument('--'+name, required=True)
    parser.add_argument('action', choices=['tick', 'observe', 'reconcile', 'cancel'])
    args = parser.parse_args()
    c = None
    try:
        c = Continuation(args.state)
        adapter = LocalProcess(c, args.host_policy, args.host_policy_sha256)
        flow = Flow(c, adapter, args.flow_policy, args.flow_policy_sha256)
        print(json.dumps({'ok': True, 'result': flow.run(args.action)}))
    except (ValueError, TypeError, OSError, sqlite3.DatabaseError) as error:
        print(json.dumps({'ok': False, 'error': type(error).__name__+': '+str(error)}))
        raise SystemExit(1)
    finally:
        if c is not None:
            c.db.close()


if __name__ == '__main__':
    main()
