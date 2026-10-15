#!/usr/bin/env python3
"""Independent native contract. No providers, mocks, network or suite-of-40.

Run: python3 automation-trial/trusted/quality_guard.py --repo automation-trial/candidate
All state, owner inputs and source fixtures live in a disposable directory.
The owner/reviewer inputs here are test authority, never worker-supplied claims.
Asserted evidence references are not machine proof: only actual Engine.verify
subprocess results may establish machine proof, through Core's existing reader.
"""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

DIMENSIONS = ('goal_alignment', 'complexity', 'architecture', 'safety', 'documentation', 'debt')
SECRET = 'RAW_BODY_QUALITY_SECRET_9f812'
REVIEWER = 'INDEPENDENT_REVIEWER_SECRET_31d4'
BUDGET = {'lease_seconds': 60, 'run_seconds': 120, 'test_seconds': 3, 'max_attempts': 2}

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def write_json(path, value):
    path.write_text(json.dumps(value, sort_keys=True))

class Contract:
    def __init__(self, repo, root):
        self.controller = repo.resolve()
        sys.path.insert(0, str(self.controller))
        from agent_workflow.core import Engine, WorkflowError, digest
        self.Engine, self.Error, self.digest = Engine, WorkflowError, digest
        self.root = root
        self.results = []
        self.fixtures = []

    def fixture(self, label, guarded=True):
        root = self.root / label
        root.mkdir()
        repo = root / 'source'
        repo.mkdir()
        subprocess.run(['git', 'init', '-q', str(repo)], check=True, capture_output=True)
        (repo / 'value.txt').write_text('good\n')
        (repo / 'check.py').write_text("from pathlib import Path\nimport sys\nprint('VERIFIER_RAW_LOG_SECRET')\nsys.exit(0 if Path('value.txt').read_text() == 'good\\n' else 7)\n")
        config = {'repo': str(repo), 'tests': [[sys.executable, 'check.py']], 'sources': ['local'],
                  'budget': dict(BUDGET), 'acceptance': {'require_tests': True, 'require_unchanged_candidate': True},
                  'permissions': {'execute_tests': True, 'publish': False, 'network': False}}
        policy_path, assessment_path = root / 'owner.json', root / 'assessment.json'
        policy = {'schema_version': 1, 'goals': ['overall-quality', 'quality-alias-goal'], 'assessment_path': str(assessment_path),
                  'trusted_reviewers': [REVIEWER], 'max_assessment_age_seconds': 300,
                  'allowed_advisories': ['complexity', 'documentation'], 'ui_required': False,
                  'bootstrap': {'external_key': 'baseline-proof', 'source_sha256': self.digest(repo)}}
        write_json(policy_path, policy)
        policy_path.chmod(0o444)
        if guarded:
            config['quality_policy'] = {'path': str(policy_path), 'sha256': sha(policy_path.read_bytes())}
        config_path = root / 'config.json'
        write_json(config_path, config)
        engine = self.Engine(root / 'state', config)
        f = {'root': root, 'repo': repo, 'config': config, 'config_path': config_path,
                'policy': policy, 'policy_path': policy_path, 'assessment_path': assessment_path, 'e': engine}
        self.fixtures.append(f)
        return f

    def task(self, f, key):
        tid = f['e'].import_task(key, 'Opaque task', SECRET)['id']
        f.setdefault('query_task', tid)
        return tid

    def owned(self, run):
        return {'run_id': run['id'], 'token': run['token']}

    def assessment(self, f, proof, candidates=None, changes=None, **overrides):
        dims = {d: {'status': 'pass', 'reason': SECRET, 'evidence': ['repo:check.py']} for d in DIMENSIONS}
        dims['ui'] = {'status': 'not_applicable', 'reason': SECRET, 'evidence': []}
        if changes:
            for dimension, status in changes.items():
                dims[dimension]['status'] = status
        value = {'schema_version': 1, 'source_sha256': self.digest(f['repo']), 'proof_run_id': proof,
                 'reviewer': REVIEWER, 'created_at': time.time(), 'dimensions': dims,
                 'candidates': candidates or []}
        value.update(overrides)
        write_json(f['assessment_path'], value)
        return value

    def candidate(self, task, kind='feature', finding='a'*64):
        row = next(dict(f['e'].db.execute('SELECT * FROM tasks WHERE id=?', (task,)).fetchone())
                   for f in self.fixtures if f['e'].db.execute('SELECT 1 FROM tasks WHERE id=?', (task,)).fetchone())
        payload = {k: row[k] for k in ('source', 'external_key', 'title', 'body', 'revision')}
        return {'task_id': task, 'task_revision': row['revision'],
                'task_payload_sha256': sha(json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()),
                'goal_id': 'overall-quality', 'finding_id': finding,
                'kind': kind, 'rationale': SECRET, 'evidence': ['repo:check.py']}

    def baseline(self, f, worker='worker'):
        tid = self.task(f, 'baseline-proof')
        run = f['e'].claim(worker, tid)
        own = self.owned(run)
        f['e'].change(**own)
        assert f['e'].verify(**own)['passed'] is True
        f['e'].transition(**own, target='ready_review')
        f['e'].transition(**own, target='accepted')
        return run['id']

    def denied(self, fn):
        try:
            fn()
        except self.Error:
            return
        raise AssertionError('operation unexpectedly admitted')

    def observe(self, f, disposition=None):
        e = f['e']
        before = e.db.execute('SELECT count(*) FROM events').fetchone()[0]
        first, second = e.next_step(task_id=f['query_task']), e.next_step(task_id=f['query_task'])
        assert first == second, 'read-only queries changed response/intent'
        assert before == e.db.execute('SELECT count(*) FROM events').fetchone()[0], 'query wrote history'
        q = first['quality']
        assert set(q) == {'disposition', 'reason', 'resume_conditions', 'source_sha256', 'policy_sha256',
                          'assessment_sha256', 'next_task_id', 'claim_allowed', 'request_id', 'host_request'}
        assert q['disposition'] in {'continue', 'repair_or_tidy', 'wait_review', 'wait_owner', 'stop_no_value'}
        if disposition:
            assert q['disposition'] == disposition, (disposition, q)
        raw = json.dumps(first)
        for private in (SECRET, REVIEWER, str(f['policy_path']), 'VERIFIER_RAW_LOG_SECRET', 'trusted_reviewers', '"token"', '"roles"'):
            assert private not in raw, 'public guidance leaked privileged input'
        assert e.config['budget'] == BUDGET
        return first

    def transport(self, f, expected):
        prefix = [sys.executable, '-m', 'agent_workflow', '--state', str(f['root'] / 'state')]
        env = dict(os.environ, PYTHONPATH=str(self.controller))
        selector = {'task_id': f['query_task']}
        cli = subprocess.run(prefix + ['next_step', '--args', json.dumps(selector)], env=env, text=True, capture_output=True, timeout=5)
        assert cli.returncode == 0, cli.stdout + cli.stderr
        assert json.loads(cli.stdout)['result'] == expected
        requests = [{'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {}},
                    {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
                    {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {'name': 'next_step', 'arguments': selector}}]
        mcp = subprocess.run(prefix + ['mcp'], input=''.join(json.dumps(r)+'\n' for r in requests),
                             env=env, text=True, capture_output=True, timeout=5)
        assert mcp.returncode == 0, mcp.stderr
        responses = [json.loads(line) for line in mcp.stdout.splitlines()]
        result = next(r['result'] for r in responses if r.get('id') == 2)
        assert result['isError'] is False
        assert json.loads(result['content'][0]['text']) == expected

    def run(self):
        # Legacy exact response shape remains without the appended quality field.
        f = self.fixture('legacy', False)
        tid = self.task(f, 'legacy-feature')
        legacy = f['e'].next_step(task_id=tid)
        assert 'quality' not in legacy
        self.transport(f, legacy)
        assert f['e'].claim('legacy-worker', tid)['task_id'] == tid
        self.results.append('legacy native claim and CLI/stdin-MCP response equality')

        f = self.fixture('baseline')
        proof = self.baseline(f)
        self.observe(f, 'wait_review')
        self.assessment(f, proof)
        self.transport(f, self.observe(f, 'stop_no_value'))
        restarted = self.Engine(f['root'] / 'state')
        f['e'] = restarted
        self.observe(f, 'stop_no_value')
        tid = self.task(f, 'novel-feature')
        self.assessment(f, proof, [self.candidate(tid)])
        q = self.observe(f, 'continue')['quality']
        assert q['claim_allowed'] is True and q['next_task_id'] == tid
        # Accepted proof must be actual and source bound; source drift cannot continue.
        (f['repo'] / 'value.txt').write_text('bad\n')
        self.observe(f, 'repair_or_tidy')
        n = f['e'].db.execute('SELECT count(*) FROM runs').fetchone()[0]
        self.denied(lambda: f['e'].claim('worker', tid))
        assert n == f['e'].db.execute('SELECT count(*) FROM runs').fetchone()[0]
        self.results.append('fresh accepted source proof, restart stop, source drift, denied feature spends no attempt')

        # All semantic dimensions independently matter; no score averaging.
        f = self.fixture('semantic')
        proof = self.baseline(f)
        tid = self.task(f, 'semantic-feature')
        candidate = self.candidate(tid)
        for dim in DIMENSIONS:
            self.assessment(f, proof, [candidate], {dim: 'block'})
            self.observe(f, 'repair_or_tidy')
        self.assessment(f, proof, [candidate], {'architecture': 'unknown'})
        self.observe(f, 'wait_review')
        self.assessment(f, proof, [candidate], {'documentation': 'advisory', 'complexity': 'advisory'})
        self.observe(f, 'continue')
        self.assessment(f, proof, [candidate], {'debt': 'advisory'})
        assert self.observe(f)['quality']['disposition'] != 'continue'
        self.assessment(f, proof, [candidate], reviewer='worker')
        assert self.observe(f)['quality']['disposition'] != 'continue'
        self.denied(lambda: f['e'].claim('worker', tid))
        self.assessment(f, proof, [candidate], created_at=time.time()+61)
        assert self.observe(f)['quality']['disposition'] != 'continue'
        self.assessment(f, proof, [candidate], created_at=time.time()-301)
        self.observe(f, 'wait_review')
        f['assessment_path'].write_text('{broken')
        assert self.observe(f)['quality']['disposition'] != 'continue'
        self.assessment(f, proof, [candidate], proof_run_id='run-nonexistent')
        assert self.observe(f)['quality']['disposition'] != 'continue'
        forged = copy.deepcopy(candidate)
        forged['task_payload_sha256'] = '0'*64
        self.assessment(f, proof, [forged])
        self.denied(lambda: f['e'].claim('worker', tid))
        forged = copy.deepcopy(candidate)
        forged['goal_id'] = 'unapproved-goal'
        self.assessment(f, proof, [forged])
        self.denied(lambda: f['e'].claim('worker', tid))
        missing = self.assessment(f, proof, [candidate])
        missing['dimensions']['safety']['evidence'] = []
        write_json(f['assessment_path'], missing)
        assert self.observe(f)['quality']['disposition'] != 'continue'
        malformed = self.assessment(f, proof, [candidate])
        del malformed['dimensions']['safety']
        write_json(f['assessment_path'], malformed)
        assert self.observe(f)['quality']['disposition'] != 'continue'
        self.assessment(f, proof, [candidate], unapproved_extra=SECRET)
        assert self.observe(f)['quality']['disposition'] != 'continue'
        valid = self.assessment(f, proof, [candidate])
        raw = json.dumps(valid)
        f['assessment_path'].write_text('{"schema_version":1,' + raw[1:])
        assert self.observe(f)['quality']['disposition'] != 'continue'
        self.results.append('six blocking dimensions, unknown, evidence-backed allowed advisories, debt advisory, reviewer/age/shape/proof rejection')

        f = self.fixture('independence')
        proof = self.baseline(f, REVIEWER)
        tid = self.task(f, 'self-reviewed-feature')
        self.assessment(f, proof, [self.candidate(tid)])
        assert self.observe(f)['quality']['disposition'] != 'continue'
        self.denied(lambda: f['e'].claim('different-worker', tid))
        self.results.append('allowlisted reviewer cannot review own actual machine-proof run')

        # Privileged owner policy mutations cannot be adopted by owned work.
        f = self.fixture('integrity')
        proof = self.baseline(f)
        tid = self.task(f, 'integrity-feature')
        self.assessment(f, proof, [self.candidate(tid)])
        run = f['e'].claim('worker', tid)
        f['e'].change(**self.owned(run))
        assert f['e'].verify(**self.owned(run))['passed']
        f['e'].transition(**self.owned(run), target='ready_review')
        f['policy_path'].chmod(0o644)
        raw = f['policy_path'].read_bytes()
        f['policy_path'].write_bytes(raw+b' ')
        self.observe(f, 'wait_owner')
        self.denied(lambda: f['e'].change(**self.owned(run)))
        self.denied(lambda: f['e'].transition(**self.owned(run), target='accepted'))
        f['policy_path'].unlink()
        self.observe(f, 'wait_owner')
        self.denied(lambda: f['e'].heartbeat(**self.owned(run)))
        self.results.append('tampered/missing owner policy and owned mutation fencing')

        f = self.fixture('bootstrap')
        tid = self.task(f, 'baseline-proof')
        wrong = self.task(f, 'baseline-proof-alias')
        self.denied(lambda: f['e'].claim('worker', wrong))
        (f['repo'] / 'value.txt').write_text('bad\n')
        self.denied(lambda: f['e'].claim('worker', tid))
        (f['repo'] / 'value.txt').write_text('good\n')
        run = f['e'].claim('worker', tid)
        (f['repo'] / 'value.txt').write_text('bad\n')
        self.denied(lambda: f['e'].change(**self.owned(run)))
        (f['repo'] / 'value.txt').write_text('good\n')
        f['e'].change(**self.owned(run))
        assert f['e'].verify(**self.owned(run))['passed']
        f['e'].transition(**self.owned(run), target='ready_review')
        (f['repo'] / 'extra').write_text('source mutation')
        self.denied(lambda: f['e'].transition(**self.owned(run), target='accepted'))
        self.results.append('bootstrap cannot change source at change or acceptance')

        # Complete repair loop, using the configured real subprocess verifier.
        f = self.fixture('repair')
        proof = self.baseline(f)
        repair = self.task(f, 'reviewed-repair')
        alias = self.task(f, 'alias-repair')
        (f['repo'] / 'value.txt').write_text('bad\n')
        finding = 'b'*64
        self.assessment(f, proof, [self.candidate(repair, 'repair', finding)], {'safety': 'block'})
        assert self.observe(f, 'repair_or_tidy')['quality']['claim_allowed']
        run = f['e'].claim('worker', repair)
        own = self.owned(run)
        f['e'].change(**own)
        fail = f['e'].verify(**own)
        assert fail['passed'] is False
        assert any(c['exit_code'] == 7 for c in fail['evidence']['results'])
        f['e'].fail(**own, reason='retry native failed verifier')
        alias_candidate = self.candidate(alias, 'repair', finding)
        alias_candidate['goal_id'] = 'quality-alias-goal'
        self.assessment(f, proof, [alias_candidate], {'safety': 'block'})
        self.denied(lambda: f['e'].claim('worker', alias))
        self.assessment(f, proof, [self.candidate(repair, 'repair', finding)], {'safety': 'block'})
        run2 = f['e'].claim('worker', repair)
        assert run2['deadline']-run2['started'] == BUDGET['run_seconds']
        assert f['e'].db.execute('SELECT count(*) FROM runs WHERE task_id=?', (repair,)).fetchone()[0] == 2
        own2 = self.owned(run2)
        f['e'].change(**own2)
        assert f['e'].verify(**own2)['passed'] is False
        (f['repo'] / 'value.txt').write_text('good\n')
        f['e'].change(**own2)
        assert f['e'].verify(**own2)['passed'] is True
        f['e'].transition(**own2, target='ready_review')
        f['query_task'] = repair
        self.observe(f, 'wait_review')
        self.assessment(f, run2['id'])
        f['e'].transition(**own2, target='accepted')
        self.observe(f, 'stop_no_value')
        f['e'] = self.Engine(f['root'] / 'state')
        self.transport(f, self.observe(f, 'stop_no_value'))
        self.results.append('native repair claim/fail, alias rejection, same-task retry, repair pass/review/accept/restart stop')

        # Attempt exhaustion cannot be refunded by changing review inputs.
        f = self.fixture('budget')
        proof = self.baseline(f)
        tid = self.task(f, 'budget-repair')
        finding = 'c'*64
        self.assessment(f, proof, [self.candidate(tid, 'repair', finding)], {'debt': 'block'})
        for _ in range(BUDGET['max_attempts']):
            run = f['e'].claim('worker', tid)
            f['e'].fail(**self.owned(run), reason='native retry budget')
        self.assessment(f, proof, [self.candidate(tid, 'repair', 'd'*64)], {'debt': 'block'})
        self.denied(lambda: f['e'].claim('worker', tid))
        assert f['e'].db.execute('SELECT count(*) FROM runs WHERE task_id=?', (tid,)).fetchone()[0] == BUDGET['max_attempts']
        self.results.append('unchanged attempt/run/lease/test budgets with exhausted-task rejection')

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repo', type=Path, required=True)
    args = parser.parse_args()
    start = time.monotonic()
    contract = None
    try:
        with tempfile.TemporaryDirectory(prefix='quality-native-contract-') as temp:
            contract = Contract(args.repo, Path(temp))
            contract.run()
        result = {'passed': True, 'cases': contract.results, 'elapsed_seconds': round(time.monotonic()-start, 3),
                  'model_calls': 0, 'network_calls': 0, 'external_api_calls': 0}
        print(json.dumps(result, ensure_ascii=False))
    except Exception as error:
        print(json.dumps({'passed': False, 'completed_cases': contract.results if contract else [],
                          'error_type': type(error).__name__, 'error': str(error),
                          'elapsed_seconds': round(time.monotonic()-start, 3),
                          'model_calls': 0, 'network_calls': 0, 'external_api_calls': 0}, ensure_ascii=False))
        return 1
    return 0

if __name__ == '__main__':
    sys.exit(main())
