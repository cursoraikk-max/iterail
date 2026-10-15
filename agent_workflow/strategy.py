"""Owner-bound retrospective context replay ledger; no Core or model authority."""
import argparse
import contextlib
import copy
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tempfile
try:
    import fcntl
except ImportError:
    fcntl = None


POLICY_FIELDS = {'schema_version', 'parent_goal', 'goal_ids', 'constraints', 'objective',
                 'min_reduction', 'evaluator_id', 'code_version', 'history_refs',
                 'evidence_root', 'artifact_registry'}
FROZEN_FIELDS = POLICY_FIELDS - {'evidence_root', 'artifact_registry'}
PROPOSAL_FIELDS = FROZEN_FIELDS | {'id', 'hypothesis', 'baseline_ref', 'candidate_ref'}
COST_FIELDS = {'elapsed_seconds', 'input_tokens', 'output_tokens', 'money', 'retries',
               'verifier_seconds', 'cleanup_seconds', 'review_seconds', 'discarded_attempts'}
ARTIFACT_FIELDS = {'schema_version', 'evaluator_id', 'code_version', 'parent_goal',
                   'goal_ids', 'variant', 'results', 'costs'}
ZERO = '0' * 64
MAX_FILE_BYTES = 1048576
MAX_LEDGER_BYTES = 4194304


def require(condition, message):
    if not condition:
        raise ValueError(message)


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False).encode('utf-8')


def canonical(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def members(pairs):
    value = {}
    for key, item in pairs:
        require(key not in value, 'duplicate JSON member')
        value[key] = item
    return value


def invalid_constant(value):
    raise ValueError('nonfinite JSON number')


def decode(data):
    return json.loads(data, object_pairs_hook=members, parse_constant=invalid_constant)


def hash_value(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def text(value):
    return isinstance(value, str) and bool(value.strip()) and len(value.encode('utf-8')) <= 4096


def ref_path(value):
    require(text(value) and '\\' not in value and '\0' not in value, 'invalid relative reference')
    path = PurePosixPath(value)
    require(not path.is_absolute() and str(path) == value and all(p not in {'.', '..'} for p in path.parts),
            'unsafe relative reference')
    return path


def read_file(path, limit=MAX_FILE_BYTES):
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(fd, 'rb') as stream:
        require(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), 'regular file required')
        raw = stream.read(limit + 1)
    require(len(raw) <= limit, 'file byte bound exceeded')
    return raw


class StrategyStore:
    """Persistent fixed-cohort decisions with cooperative POSIX file locking."""

    def __init__(self, store_root, *, trusted_policy, owner_policy_sha256=None):
        require(fcntl is not None, 'strategy store requires POSIX file locking')
        self.policy = copy.deepcopy(trusted_policy)
        self._validate_policy()
        require(owner_policy_sha256 is None or hash_value(owner_policy_sha256), 'invalid owner policy file SHA')
        raw_root = Path(store_root)
        require(not raw_root.is_symlink(), 'store root symlink')
        existed_before = raw_root.exists()
        self.root = raw_root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.policy_sha = canonical(self.policy)
        self.binding = {'schema_version': 1, 'policy': self.policy, 'policy_sha256': self.policy_sha,
                        'owner_policy_sha256': owner_policy_sha256,
                        'implementation_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
        with self._locked():
            paths = [self.root / name for name in ('binding.json', 'head.json', 'ledger.jsonl')]
            if not any(p.exists() or p.is_symlink() for p in paths):
                require(not existed_before, 'existing store directory is missing initialized history')
                require(set(p.name for p in self.root.iterdir()) <= {'.lock'}, 'unrecognized existing store history')
                fd = os.open(paths[2], os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
                self._atomic('head.json', {'schema_version': 1, 'seq': 0, 'last_sha256': ZERO, 'ledger_bytes': 0})
                self._atomic('binding.json', self.binding)
            self._load()

    def _validate_policy(self):
        p = self.policy
        require(isinstance(p, dict) and set(p) == POLICY_FIELDS, 'invalid trusted policy fields')
        require(type(p['schema_version']) is int and p['schema_version'] == 1, 'unsupported policy schema')
        require(text(p['parent_goal']) and isinstance(p['goal_ids'], list) and 0 < len(p['goal_ids']) <= 20
                and all(text(x) for x in p['goal_ids']) and len(set(p['goal_ids'])) == len(p['goal_ids']), 'invalid fixed cohort')
        require(isinstance(p['constraints'], dict), 'fixed constraints required')
        require(p['objective'] == 'bytes_no_coverage_regression' and p['evaluator_id'] == 'context-coverage-v1', 'unsupported evaluator')
        require(type(p['min_reduction']) in (int, float) and p['min_reduction'] == 0.1, 'fixed reduction threshold required')
        require(hash_value(p['code_version']), 'invalid code version')
        require(isinstance(p['evidence_root'], str) and Path(p['evidence_root']).is_absolute()
                and not Path(p['evidence_root']).is_symlink(), 'absolute owner evidence root required')
        self.evidence_root = Path(p['evidence_root']).resolve()
        require(isinstance(p['history_refs'], list) and 0 < len(p['history_refs']) <= 32, 'historical rationale required')
        history_paths = []
        for ref in p['history_refs']:
            require(isinstance(ref, dict) and set(ref) == {'path', 'sha256'} and hash_value(ref['sha256']), 'invalid historical reference')
            ref_path(ref['path']); history_paths.append(ref['path'])
        require(len(set(history_paths)) == len(history_paths), 'duplicate historical reference')
        require(isinstance(p['artifact_registry'], dict) and 0 < len(p['artifact_registry']) <= 128, 'artifact registry required')
        for ref, sha in p['artifact_registry'].items():
            ref_path(ref); require(hash_value(sha), 'invalid registry artifact SHA')
        require(len(encoded(p)) <= MAX_FILE_BYTES, 'policy byte bound exceeded')

    @contextlib.contextmanager
    def _locked(self):
        fd = os.open(self.root / '.lock', os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        try:
            require(stat.S_ISREG(os.fstat(fd).st_mode), 'regular lock file required')
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def _atomic(self, name, value):
        raw = encoded(value) + b'\n'
        fd, temporary = tempfile.mkstemp(prefix='.write-', dir=self.root)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(raw); stream.flush(); os.fsync(stream.fileno())
            target = self.root / name
            require(not target.is_symlink(), 'store file symlink')
            os.replace(temporary, target)
            directory = os.open(self.root, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _proposal(self, proposal):
        require(isinstance(proposal, dict) and set(proposal) == PROPOSAL_FIELDS, 'invalid proposal fields')
        require(type(proposal['schema_version']) is int and type(proposal['min_reduction']) in (int, float), 'invalid proposal numeric types')
        require(all(encoded(proposal[k]) == encoded(self.policy[k]) for k in FROZEN_FIELDS), 'proposal changes frozen policy')
        require(text(proposal['id']) and text(proposal['hypothesis']), 'proposal identity and hypothesis required')
        for key in ('baseline_ref', 'candidate_ref'):
            ref_path(proposal[key])
            require(proposal[key] in self.policy['artifact_registry'], 'unregistered artifact reference')
        require(len(encoded(proposal)) <= 65536, 'proposal byte bound exceeded')
        return canonical({'code_version': self.policy['code_version'], 'goal_ids': self.policy['goal_ids'],
                          'baseline': self.policy['artifact_registry'][proposal['baseline_ref']],
                          'candidate': self.policy['artifact_registry'][proposal['candidate_ref']]})

    def _bound_bytes(self, ref, expected):
        path = self.evidence_root
        for part in ref_path(ref).parts:
            path = path / part
            require(not path.is_symlink(), 'evidence reference symlink')
        require(path.resolve().is_relative_to(self.evidence_root), 'evidence reference escapes root')
        raw = read_file(path)
        require(hashlib.sha256(raw).hexdigest() == expected, 'owner artifact SHA changed')
        return raw

    def _artifact(self, ref):
        data = decode(self._bound_bytes(ref, self.policy['artifact_registry'][ref]))
        require(isinstance(data, dict) and set(data) == ARTIFACT_FIELDS, 'invalid machine artifact fields')
        require(type(data['schema_version']) is int and data['schema_version'] == 1, 'invalid artifact schema')
        require(all(data[k] == self.policy[k] for k in ('evaluator_id', 'code_version', 'parent_goal', 'goal_ids')),
                'artifact changes fixed evaluator/cohort/version')
        require(text(data['variant']) and isinstance(data['results'], list), 'invalid artifact results')
        goals = []
        for row in data['results']:
            require(isinstance(row, dict) and set(row) == {'goal_id', 'success', 'coverage', 'used_bytes'}, 'invalid result fields')
            require(text(row['goal_id']) and type(row['success']) is bool
                    and all(type(row[k]) is int and row[k] >= 0 for k in ('coverage', 'used_bytes')), 'unknown or invalid result metrics')
            goals.append(row['goal_id'])
        require(len(goals) == len(self.policy['goal_ids']) and set(goals) == set(self.policy['goal_ids']), 'incomplete or duplicate goal denominator')
        costs = data['costs']
        require(isinstance(costs, dict) and set(costs) == COST_FIELDS, 'incomplete cost evidence')
        for cost in costs.values():
            require(cost is None or (type(cost) is int and cost >= 0)
                    or (type(cost) is float and math.isfinite(cost) and cost >= 0), 'invalid cost value')
        require(costs['elapsed_seconds'] is not None, 'elapsed time unknown')
        return data

    def _report(self, proposal, prior_count):
        for ref in self.policy['history_refs']:
            self._bound_bytes(ref['path'], ref['sha256'])
        baseline = self._artifact(proposal['baseline_ref'])
        candidate = self._artifact(proposal['candidate_ref'])
        base = {x['goal_id']: x for x in baseline['results']}
        cand = {x['goal_id']: x for x in candidate['results']}
        base_bytes = sum(x['used_bytes'] for x in base.values())
        cand_bytes = sum(x['used_bytes'] for x in cand.values())
        success = all(x['success'] for x in cand.values())
        coverage = all(cand[g]['coverage'] >= base[g]['coverage'] for g in self.policy['goal_ids'])
        gain = base_bytes > 0 and (base_bytes - cand_bytes) * 10 >= base_bytes
        retained = success and coverage and gain
        count = 0 if retained else prior_count + 1
        decision = 'retain' if retained else ('stop' if count >= 2 else 'rollback')
        reason = ('fixed goals passed with no coverage regression and at least 10% fewer context bytes'
                  if retained else ('candidate goal failed' if not success else
                                    ('coverage regressed' if not coverage else 'byte reduction below fixed 10% threshold')))
        if decision == 'stop':
            reason = 'two distinct consecutive no-gain comparisons; ' + reason
        refs = [{'role': 'baseline', 'path': proposal['baseline_ref'], 'sha256': self.policy['artifact_registry'][proposal['baseline_ref']]},
                {'role': 'candidate', 'path': proposal['candidate_ref'], 'sha256': self.policy['artifact_registry'][proposal['candidate_ref']]}]
        refs += [dict(ref, role='historical_rationale') for ref in self.policy['history_refs']]
        costs = list(baseline['costs'].values()) + list(candidate['costs'].values())
        return {'schema_version': 1, 'registration_id': proposal['id'], 'proposal_sha256': canonical(proposal),
                'policy_sha256': self.policy_sha, 'code_version': self.policy['code_version'],
                'evaluator_id': self.policy['evaluator_id'], 'parent_goal': self.policy['parent_goal'],
                'goal_ids': copy.deepcopy(self.policy['goal_ids']), 'denominator': len(base), 'paired_count': len(base),
                'decision': decision, 'reason': reason, 'no_gain_count': count, 'model_benefit': False,
                'cost_status': 'unknown' if any(x is None for x in costs) else 'known',
                'baseline_costs': baseline['costs'], 'candidate_costs': candidate['costs'], 'evidence_refs': refs,
                'baseline_bytes': base_bytes, 'candidate_bytes': cand_bytes}

    def _load(self):
        require(encoded(decode(read_file(self.root / 'binding.json'))) == encoded(self.binding), 'store policy or implementation binding changed')
        head = decode(read_file(self.root / 'head.json'))
        require(isinstance(head, dict) and set(head) == {'schema_version', 'seq', 'last_sha256', 'ledger_bytes'}
                and type(head['schema_version']) is int and head['schema_version'] == 1
                and type(head['seq']) is int and head['seq'] >= 0 and type(head['ledger_bytes']) is int
                and head['ledger_bytes'] >= 0 and hash_value(head['last_sha256']), 'invalid ledger head')
        raw = read_file(self.root / 'ledger.jsonl', MAX_LEDGER_BYTES)
        require(len(raw) == head['ledger_bytes'] and (not raw or raw.endswith(b'\n')), 'missing or truncated ledger history')
        records = []; registrations = {}; pairs = set(); reports = []; count = 0; stopped = False; previous = ZERO
        for line in raw.splitlines():
            record = decode(line)
            require(isinstance(record, dict) and set(record) == {'schema_version', 'seq', 'prev_sha256', 'kind', 'payload', 'sha256'}
                    and type(record['schema_version']) is int and record['schema_version'] == 1
                    and type(record['seq']) is int and record['seq'] == len(records) + 1
                    and record['prev_sha256'] == previous and record['sha256'] == canonical({k: v for k, v in record.items() if k != 'sha256'}),
                    'corrupt ledger record or chain')
            payload = record['payload']
            if record['kind'] == 'registration':
                require(not stopped and isinstance(payload, dict) and set(payload) == {'proposal', 'proposal_sha256', 'pair_sha256'}, 'invalid registration record')
                proposal = payload['proposal']; pair = self._proposal(proposal)
                require(payload['proposal_sha256'] == canonical(proposal) and payload['pair_sha256'] == pair
                        and proposal['id'] not in registrations and pair not in pairs, 'registration identity changed or replayed pair')
                registrations[proposal['id']] = payload; pairs.add(pair)
            elif record['kind'] == 'decision':
                require(not stopped and isinstance(payload, dict) and set(payload) == {'registration_id', 'report', 'report_sha256'}, 'invalid decision record')
                ident = payload['registration_id']
                require(ident in registrations and not any(x['registration_id'] == ident for x in reports), 'decision lacks unique registration')
                report = self._report(registrations[ident]['proposal'], count)
                require(encoded(payload['report']) == encoded(report) and payload['report_sha256'] == canonical(report), 'corrupt or unbound decision history')
                reports.append(report); count = report['no_gain_count']; stopped = report['decision'] == 'stop'
            else:
                raise ValueError('unknown ledger event')
            records.append(record); previous = record['sha256']
        require(len(records) <= 256 and head['seq'] == len(records) and head['last_sha256'] == previous, 'ledger head/history mismatch')
        return {'records': records, 'registrations': registrations, 'pairs': pairs, 'reports': reports,
                'no_gain_count': count, 'stopped': stopped, 'head': head}

    def _append(self, state, kind, payload):
        require(len(state['records']) < 256, 'ledger capacity exhausted')
        record = {'schema_version': 1, 'seq': state['head']['seq'] + 1,
                  'prev_sha256': state['head']['last_sha256'], 'kind': kind, 'payload': payload}
        record['sha256'] = canonical(record)
        raw = encoded(record) + b'\n'
        require(state['head']['ledger_bytes'] + len(raw) <= MAX_LEDGER_BYTES, 'ledger byte bound exceeded')
        fd = os.open(self.root / 'ledger.jsonl', os.O_WRONLY | os.O_APPEND | getattr(os, 'O_NOFOLLOW', 0))
        try:
            view = memoryview(raw)
            while view:
                written = os.write(fd, view); require(written > 0, 'ledger append failed'); view = view[written:]
            os.fsync(fd)
        finally:
            os.close(fd)
        self._atomic('head.json', {'schema_version': 1, 'seq': record['seq'], 'last_sha256': record['sha256'],
                                   'ledger_bytes': state['head']['ledger_bytes'] + len(raw)})

    def preregister(self, proposal):
        proposal = copy.deepcopy(proposal)
        pair = self._proposal(proposal)
        result = {'id': proposal['id'], 'proposal_sha256': canonical(proposal), 'policy_sha256': self.policy_sha}
        with self._locked():
            state = self._load()
            old = state['registrations'].get(proposal['id'])
            if old is not None:
                require(old['proposal_sha256'] == canonical(proposal), 'registration ID changed content')
                return result
            require(not state['stopped'], 'strategy replay stopped')
            require(pair not in state['pairs'], 'artifact pair already registered')
            self._append(state, 'registration', {'proposal': proposal, 'proposal_sha256': result['proposal_sha256'], 'pair_sha256': pair})
        return result

    def evaluate(self, registration_id):
        require(text(registration_id), 'registration ID required')
        with self._locked():
            state = self._load()
            require(registration_id in state['registrations'], 'unknown registration')
            for report in state['reports']:
                if report['registration_id'] == registration_id:
                    return copy.deepcopy(report)
            require(not state['stopped'], 'strategy replay stopped')
            report = self._report(state['registrations'][registration_id]['proposal'], state['no_gain_count'])
            self._append(state, 'decision', {'registration_id': registration_id, 'report': report, 'report_sha256': canonical(report)})
        return copy.deepcopy(report)

    def history(self):
        with self._locked():
            return copy.deepcopy(self._load()['reports'])


def main():
    parser = argparse.ArgumentParser(description='Owner-bound retrospective local context replay; no model calls')
    parser.add_argument('--store', required=True, type=Path)
    parser.add_argument('--policy', required=True, type=Path)
    parser.add_argument('--policy-sha256', required=True)
    sub = parser.add_subparsers(dest='action', required=True)
    register = sub.add_parser('preregister'); register.add_argument('--proposal', required=True, type=Path)
    evaluate = sub.add_parser('evaluate'); evaluate.add_argument('--id', required=True)
    sub.add_parser('history')
    args = parser.parse_args()
    try:
        require(args.store.is_absolute() and args.policy.is_absolute(), 'absolute store and owner policy paths required')
        repo = Path(__file__).resolve().parents[1]
        require(not args.store.resolve().is_relative_to(repo), 'store must be outside source checkout')
        require(not args.policy.resolve().is_relative_to(repo) and not args.policy.resolve().is_relative_to(args.store.resolve()),
                'owner policy must be outside checkout and store')
        raw = read_file(args.policy)
        require(hash_value(args.policy_sha256) and hashlib.sha256(raw).hexdigest() == args.policy_sha256, 'owner policy file SHA mismatch')
        store = StrategyStore(args.store, trusted_policy=decode(raw), owner_policy_sha256=args.policy_sha256)
        if args.action == 'preregister':
            result = store.preregister(decode(read_file(args.proposal, 65536)))
        elif args.action == 'evaluate':
            result = store.evaluate(args.id)
        else:
            result = store.history()
        print(json.dumps({'ok': True, 'result': result}, ensure_ascii=False, allow_nan=False))
    except (ValueError, TypeError, OSError, RecursionError) as error:
        print(json.dumps({'ok': False, 'error': str(error)}))
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
