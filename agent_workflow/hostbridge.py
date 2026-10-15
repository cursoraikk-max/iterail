"""Durable caller-side dispatch boundaries over an existing Continuation ledger.

No platform or model is called here. A committed ticket is not execution evidence;
only Core history and independent owner receipts can reconcile the result.
"""
import re
from pathlib import Path
from .continuation import absolute, digest, encoded, hash_value, load, regular, require, sha
from .diagnostics import validate_host_request


class HostBridge:
    def __init__(self, continuation):
        self.c = continuation
        with self.c._locked():
            self.c._identity()
            self.c.db.execute('CREATE TABLE IF NOT EXISTS host_dispatch(request_id TEXT PRIMARY KEY, body TEXT NOT NULL)')

    @staticmethod
    def _key(packet):
        return sha(encoded({k: v for k, v in packet.items() if k != 'run_id'}))

    @staticmethod
    def _public(entry):
        keys = ('request_id', 'kind', 'dispatch_key', 'actor', 'phase', 'task_id', 'run_id', 'dispatch_started')
        result = {key: entry[key] for key in keys}
        if entry['receipt_sha256'] is not None: result['receipt_sha256'] = entry['receipt_sha256']
        return result

    def _get(self, request_id):
        require(isinstance(request_id, str) and re.fullmatch(r'request-[0-9a-f]{24}', request_id), 'existing dispatch request required')
        row = self.c.db.execute('SELECT CASE WHEN length(body)<=1048576 THEN body END FROM host_dispatch WHERE request_id=?', (request_id,)).fetchone()
        require(row is not None and row[0] is not None, 'existing bounded dispatch record required')
        entry = load(row[0])
        require(entry['request_id'] == request_id and entry['dispatch_key'] == self._key(entry['packet'])
                and entry['actor'] == 'host-'+entry['dispatch_key'][:32], 'dispatch identity changed')
        return entry

    def _snapshot(self, request_id):
        with self.c._locked():
            row = self.c._row(); require(self.c._gate(row) is None, 'original continuation stopped or expired')
            return self._get(request_id), row

    def _pending(self, entry, row):
        packet = load(row['request']) if row['request'] else None
        require(packet is not None and packet['id'] == entry['request_id'] and self._key(packet) == entry['dispatch_key'], 'current durable request identity required')
        version = load(row['active_version'])
        validate_host_request(packet, version)
        require(version == entry['version'] and (entry['kind'] == 'worker' or packet['run_id'] == entry['packet']['run_id']), 'current dispatch version or run changed')
        return packet

    def _commit(self, before, changes, event, *, pending=False, live=True):
        with self.c._locked():
            row = self.c._row()
            if live: require(self.c._gate(row) is None, 'original continuation stopped or expired')
            require(self._get(before['request_id']) == before, 'dispatch record CAS conflict')
            if pending: self._pending(before, row)
            after = {**before, **changes}
            with self.c._tx():
                self.c.db.execute('UPDATE host_dispatch SET body=? WHERE request_id=?', (encoded(after).decode(), after['request_id']))
                self.c._event(event, self._public(after))
            return after

    def _matches(self, entry):
        return self.c._core_read('SELECT id FROM runs WHERE task_id=? AND worker=? AND baseline=? AND workflow=? LIMIT 2',
                                (entry['task_id'], entry['actor'], entry['packet']['source_sha256'], entry['packet']['package_sha256']))

    def reserve(self):
        # tick owns its own lock; never nest a Continuation mutation inside ours.
        self.c.tick()
        with self.c._locked():
            row = self.c._row(); require(self.c._gate(row) is None, 'original continuation stopped or expired')
            packet = load(row['request']) if row['request'] else None
            require(packet is not None, 'no current dispatchable durable request')
            version = load(row['active_version']); validate_host_request(packet, version)
            key = self._key(packet)
            existing = self.c.db.execute('SELECT request_id FROM host_dispatch WHERE request_id=?', (packet['id'],)).fetchone()
            if existing:
                entry = self._get(packet['id']); self._pending(entry, row)
                return self._public(entry)
            entry = {'request_id': packet['id'], 'kind': packet['kind'], 'dispatch_key': key,
                     'actor': 'host-'+key[:32], 'phase': 'reserved', 'task_id': packet['task_id'],
                     'run_id': packet['run_id'], 'dispatch_started': False, 'packet': packet,
                     'version': version, 'receipt_sha256': None, 'finish': None}
            with self.c._tx():
                self.c.db.execute('INSERT INTO host_dispatch VALUES(?,?)', (packet['id'], encoded(entry).decode()))
                self.c._event('host_dispatch_reserved', self._public(entry))
            return self._public(entry)

    def begin(self, request_id):
        entry, row = self._snapshot(request_id); self._pending(entry, row)
        require(entry['phase'] == 'reserved' and entry['dispatch_started'] is False, 'dispatch ticket already consumed or reconciliation required')
        version = entry['version']; self.c._core_policy(); self.c._runtime(version)
        if entry['kind'] == 'worker':
            if self._matches(entry):
                self._commit(entry, {'phase': 'uncertain'}, 'host_existing_outcome_requires_reconciliation', pending=True)
                raise ValueError('actual Core outcome exists; reconcile without dispatch')
            task = self.c._core('status', {'task_id': entry['task_id']}, version)
            guidance = self.c._core('next_step', {'task_id': entry['task_id']}, version)
            require(task['state'] == 'queued' and digest(Path(self.c.policy['repo'])) == entry['packet']['source_sha256'], 'current queued request source required before dispatch')
            quality = guidance.get('quality')
            require((quality is not None and quality['claim_allowed'] and quality['next_task_id'] == entry['task_id'])
                    or (quality is None and guidance['next_action'] == 'claim'), 'current Core admission required before dispatch')
        elif entry['kind'] == 'review':
            run = self.c._core('status', {'run_id': entry['run_id']}, version)
            require(run['state'] == 'ready_review' and run['task_id'] == entry['task_id']
                    and run['workflow'] == entry['packet']['package_sha256'], 'actual unfulfilled Core review required before dispatch')
        else:
            self.c._quality_identity(entry['packet'], version)
        after = self._commit(entry, {'phase': 'executing', 'dispatch_started': True}, 'host_dispatch_begun', pending=True)
        return {**self._public(after), 'packet': entry['packet']}

    def _promoted(self, entry):
        intent = entry['finish']
        if intent is None: return False
        with self.c._locked():
            row = self.c._row(); active = load(row['active_version'])
            rows = self.c.db.execute("SELECT body FROM events WHERE kind='version_promoted' ORDER BY seq DESC LIMIT ?", (self.c.policy['budget']['max_requests'],)).fetchall()
            for record in rows:
                event = load(record[0])
                if (event['request']['id'] == entry['request_id'] and event['request']['run_id'] == entry['run_id']
                        and event['receipt_sha256'] == intent['receipt_sha256']
                        and event['previous']['id'] == intent['expected_version']
                        and event['active'] == active and active['controller_path'] == intent['controller_path']):
                    self.c._runtime(active)
                    return True
        return False

    def reconcile(self, request_id):
        # A completed row describes a historical effect, not current source quality.
        with self.c._locked(): entry = self._get(request_id)
        if entry['phase'] in {'completed', 'rejected'}: return self._public(entry)
        if entry['kind'] == 'review' and self._promoted(entry):
            return self._public(self._commit(entry, {'phase': 'completed'}, 'host_promotion_recovered', live=False))
        entry, row = self._snapshot(request_id)
        self.c._core_policy(); self.c._runtime(entry['version'])
        if entry['kind'] == 'worker':
            if entry['phase'] == 'bound': return self._public(entry)
            matches = self._matches(entry)
            if len(matches) != 1:
                return self._public(self._commit(entry, {'phase': 'uncertain'}, 'host_outcome_uncertain'))
            run_id = matches[0]['id']; pending = load(row['request']) if row['request'] else None
            if pending and pending['kind'] == 'review' and pending['run_id'] == run_id:
                require(pending['task_id'] == entry['task_id'] and pending['package_sha256'] == entry['packet']['package_sha256']
                        and pending['source_sha256'] == entry['packet']['source_sha256'], 'observed review identity changed')
            else:
                self._pending(entry, row)
                self.c.bind_run(request_id, run_id)
            return self._public(self._commit(entry, {'phase': 'bound', 'run_id': run_id}, 'host_actual_run_reconciled'))
        if entry['kind'] == 'quality_review' and entry['dispatch_started'] is True:
            require(load(row['active_version']) == entry['version'], 'current physical semantic review version required')
            # The resolver may have committed and cleared pending before its
            # caller lost the reply. Observe that exact successful effect rather
            # than executing the resolver a second time.
            self.c._quality_identity(entry['packet'], entry['version'])
            projection = self.c._quality_projection(entry['version'])
            with self.c._locked():
                resolved = self.c._resolved_quality(entry['version'], projection, entry['packet'])
            if resolved:
                return self._public(self._commit(entry, {'phase': 'completed'}, 'host_semantic_review_recovered'))
        self._pending(entry, row)
        if entry['kind'] == 'quality_review':
            try: self.c.resolve_quality_review(request_id)
            except ValueError:
                return self._public(self._commit(entry, {'phase': 'blocked'}, 'host_semantic_review_blocked'))
            return self._public(self._commit(entry, {'phase': 'completed'}, 'host_semantic_review_reconciled'))
        run = self.c._core('status', {'run_id': entry['run_id']}, entry['version'])
        require(run['task_id'] == entry['task_id'] and run['workflow'] == entry['packet']['package_sha256'], 'actual review run changed')
        return self._public(self._commit(entry, {'phase': 'awaiting_receipt'}, 'host_actual_review_observed'))

    def finish(self, request_id, receipt_path, receipt_sha256, controller_path, expected_version):
        entry, row = self._snapshot(request_id)
        require(entry['kind'] == 'review' and entry['dispatch_started'] is True and entry['phase'] not in {'reserved', 'rejected'}, 'begun independent review required')
        intent = {'receipt_path': receipt_path, 'receipt_sha256': receipt_sha256,
                  'controller_path': controller_path, 'expected_version': expected_version}
        if entry['finish'] is not None:
            require(entry['finish'] == intent, 'existing finish intent cannot be replaced')
            if self._promoted(entry):
                if entry['phase'] != 'completed': entry = self._commit(entry, {'phase': 'completed'}, 'host_promotion_recovered', live=False)
                return self._public(entry)
        self._pending(entry, row)
        require(expected_version == entry['version']['id'], 'expected physical version required')
        version = entry['version']; self.c._core_policy(); self.c._runtime(version)
        run = self.c._core('status', {'run_id': entry['run_id']}, version)
        proof = self.c._core('next_step', {'run_id': entry['run_id']}, version)
        require(run['state'] in {'ready_review', 'accepted'} and run['task_id'] == entry['task_id']
                and run['baseline'] == entry['packet']['source_sha256']
                and run['workflow'] == entry['packet']['package_sha256']
                and proof['run_id'] == run['id'] and proof['task_id'] == run['task_id']
                and proof['stored_state'] == run['state'] and proof['evidence_valid'] is True and proof['source_match'] is True,
                'actual current Core proof required for independent receipt')
        path = absolute(receipt_path)
        require(not path.is_relative_to(Path(self.c.policy['repo'])), 'independent receipt must be outside source')
        raw = regular(path); require(hash_value(receipt_sha256) and sha(raw) == receipt_sha256, 'exact receipt SHA required')
        receipt = load(raw)
        require(isinstance(receipt, dict) and set(receipt) == {'schema_version', 'approved', 'reviewer', 'run_id', 'core_state', 'source_root', 'source_sha256', 'controller_package_sha256'}
                and type(receipt['schema_version']) is int and receipt['schema_version'] == 1 and type(receipt['approved']) is bool
                and isinstance(receipt['reviewer'], str) and bool(receipt['reviewer'].strip()) and receipt['reviewer'] != run['worker']
                and receipt['run_id'] == run['id'] and receipt['core_state'] == self.c.policy['core_state']
                and receipt['source_root'] == self.c.policy['repo'] and receipt['source_sha256'] == run['candidate']
                and receipt['controller_package_sha256'] == run['workflow'], 'exact independently attributed receipt required')
        if receipt['approved'] is False:
            from .quality import load as quality_policy
            policy = self.c._core_policy()
            require('quality_policy' in policy and receipt['reviewer'] in quality_policy(policy)['trusted_reviewers'], 'pinned independent reviewer required for rejection')
            return self._public(self._commit(entry, {'phase': 'rejected', 'receipt_sha256': receipt_sha256}, 'host_independent_review_rejected', pending=True))
        require(run['state'] == 'accepted', 'actual independent Core acceptance required before promotion')
        entry = self._commit(entry, {'phase': 'finishing', 'finish': intent, 'receipt_sha256': receipt_sha256}, 'host_finish_intent', pending=True)
        self.c.promote(request_id, receipt_path, receipt_sha256, controller_path, expected_version)
        return self._public(self._commit(entry, {'phase': 'completed'}, 'host_finish_completed', live=False))

    def status(self):
        with self.c._locked():
            row = self.c._row(); limit = self.c.policy['budget']['max_requests']
            records = self.c.db.execute('SELECT request_id FROM host_dispatch ORDER BY rowid LIMIT ?', (limit+1,)).fetchall()
            require(len(records) <= limit, 'original request ceiling inconsistent with bridge history')
            return {'entries': [self._public(self._get(record[0])) for record in records],
                    **{key: row[key] for key in ('started', 'deadline', 'requests_spent')}}
