#!/usr/bin/env python3
"""Explicit native host fixtures plus real Core; no agent/provider integration."""
import argparse
import os
import subprocess
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time

P = argparse.ArgumentParser()
P.add_argument('--installed', action='store_true')
A = P.parse_args()
ROOT = Path(__file__).resolve().parents[2]
if not A.installed:
    sys.path.insert(0, str(ROOT))
import agent_workflow
from agent_workflow.continuation import Continuation, digest, encoded, load, sha
from agent_workflow.hostsupervisor import LocalProcess, OneShot, write_once
from agent_workflow.hostflow import Flow
SUBJECT = Path(agent_workflow.__file__).parent.parent
spec = importlib.util.spec_from_file_location('host_flow_setup', ROOT/'tests/portable/fixtures/research-trial/trusted/continuation_quality_guard.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def denied(operation):
    try:
        operation()
    except (ValueError, OSError):
        return
    raise AssertionError('unadmitted operation succeeded')


class JournalFixture:
    """Trusted no-process adapter; observations are explicitly simulated."""
    identity = 'native-journal-fixture-v1'

    def __init__(self, root, state='completed', lose_reply=False):
        self.root = root
        self.root.mkdir(exist_ok=True)
        self.workdir = str(root)
        self.state = state
        self.lose_reply = lose_reply

    def note(self, method, intent):
        with (self.root/'calls.jsonl').open('a') as stream:
            stream.write(json.dumps({'method': method, 'key': intent['dispatch_key']})+'\n')

    def path(self, intent):
        return self.root/(intent['dispatch_key']+'.json')

    def start(self, intent):
        self.note('start', intent)
        value = {k: intent[k] for k in ('execution_id', 'dispatch_key')}
        value.update(state=self.state, sequence=0 if self.state == 'uncertain' else 1, observed_at=time.time())
        write_once(self.path(intent), value)
        if self.lose_reply:
            raise OSError('native fixture lost start reply after durable effect')
        return value

    def observe(self, intent):
        self.note('observe', intent)
        return load(self.path(intent).read_bytes())

    def reconcile(self, intent):
        return self.observe(intent)

    def cancel(self, intent):
        self.note('cancel', intent)
        value = load(self.path(intent).read_bytes())
        if value['state'] not in {'completed', 'expired', 'failed', 'cancelled'}:
            value.update(state='cancelled', sequence=value['sequence']+1, observed_at=time.time())
            self.path(intent).write_bytes(encoded(value))
        return value

    def starts(self):
        path = self.root/'calls.jsonl'
        return [x for x in map(json.loads, path.read_text().splitlines()) if x['method'] == 'start'] if path.exists() else []


def main():
    results = []
    with tempfile.TemporaryDirectory(prefix='iterail-host-flow-') as temp:
        cases = m.Cases(SUBJECT, Path(temp).resolve())
        # Fixed test-owner admission before fixture creation, not a live policy edit.
        m.helper.BUDGET = {'lease_seconds': 60, 'run_seconds': 120, 'test_seconds': 5, 'max_attempts': 2}

        def setup(name, cap=4, slots=6, roles=None, state='completed', lost=False, real=False, create_flow=True, sleep=0, process_seconds=5):
            f = cases.fixture(name, slots)
            f['proof'] = cases.q.baseline(f)
            f['tid'] = cases.q.task(f, 'flow-work')
            cases.q.assessment(f, f['proof'], [cases.q.candidate(f['tid'])])
            work = f['root']/'host-work'
            work.mkdir()
            if real:
                script = f['root']/'native.py'
                script.write_text('from pathlib import Path\nimport os,time\nwith Path("starts.txt").open("a") as f:f.write(os.environ["ITERAIL_EXECUTION_ID"]+"\\n")\ntime.sleep('+str(sleep)+')\n')
                exe = Path(sys.executable).resolve()
                policy = {'schema_version': 1, 'fixture_only': True, 'continuation_state': str(f['continuation']),
                          'executable_path': str(exe), 'executable_sha256': sha(exe.read_bytes()),
                          'script_path': str(script), 'script_sha256': sha(script.read_bytes()),
                          'cwd': str(work), 'max_seconds': process_seconds, 'output_bytes': 2048}
                path = f['root']/'local-process.json'
                path.write_bytes(encoded(policy))
                f['host_policy_path'] = path
                f['host_policy_sha'] = sha(path.read_bytes())
                f['adapter'] = LocalProcess(f['c'], str(path), f['host_policy_sha'])
            else:
                f['adapter'] = JournalFixture(work, state, lost)
            policy = {'schema_version': 1, 'continuation_state': str(f['continuation']),
                      'continuation_identity_sha256': sha((f['continuation']/'identity.json').read_bytes()),
                      'adapter_identity': f['adapter'].identity, 'allowed_roles': roles or ['worker', 'review'],
                      'max_intents': cap, 'poll_seconds': 1}
            path = f['root']/'flow.json'
            path.write_bytes(encoded(policy))
            f.update(flow_path=path, flow_sha=sha(path.read_bytes()), original=f['c'].status(), work=work)
            if create_flow:
                f['flow'] = Flow(f['c'], f['adapter'], str(path), f['flow_sha'])
            return f

        def reopen(f):
            f['c'].db.close()
            f['c'] = Continuation(str(f['continuation']), clock=lambda: f['clock'][0])
            f['flow'] = Flow(f['c'], f['adapter'], str(f['flow_path']), f['flow_sha'])

        def due(f):
            f['clock'][0] += 2

        def resources(f, spent):
            now = f['c'].status()
            assert now['requests_spent'] == spent
            assert (now['started'], now['deadline']) == (f['original']['started'], f['original']['deadline'])

        def worker_ready(f, first):
            entry = f['flow'].bridge.status()['entries'][-1]
            version = f['c'].status()['active_version']
            run = f['c']._core('claim', {'worker': entry['actor'], 'task_id': entry['task_id']}, version)
            own = cases.q.owned(run)
            (f['repo']/'host-flow-change.txt').write_text('native real Core candidate\n')
            cases.git(f['repo'], 'add', '.')
            cases.git(f['repo'], '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                      '-c', 'commit.gpgsign=false', 'commit', '-qm', 'native candidate')
            f['c']._core('change', own, version)
            assert f['c']._core('verify', own, version)['passed']
            f['c']._core('transition', {**own, 'target': 'ready_review'}, version)
            f.update(run=run, own=own, version=version)
            due(f)
            return f['flow'].run()

        f = setup('chain')
        first = f['flow'].run()
        assert first['new_dispatches'] == 1 and first['intent']['role'] == 'worker'
        resources(f, 2)
        calls = (f['work']/'calls.jsonl').read_bytes()
        assert f['flow'].run()['phase'] == 'not_due'
        assert (f['work']/'calls.jsonl').read_bytes() == calls
        reopen(f)
        review = worker_ready(f, first)
        assert review['new_dispatches'] == 1 and review['intent']['role'] == 'review'
        assert review['intent']['request_id'] != first['intent']['request_id']
        resources(f, 2)
        reopen(f)
        due(f)
        assert f['flow'].run()['phase'] == 'awaiting_core_or_receipt'
        assert f['c']._core('status', {'run_id': f['run']['id']}, f['version'])['state'] == 'ready_review'
        # Independent owner-authored fixture assessment/receipt; not a real AI reviewer.
        cases.q.assessment(f, f['run']['id'])
        f['c']._core('transition', {**f['own'], 'target': 'accepted'}, f['version'])
        receipt = {'schema_version': 1, 'approved': True, 'reviewer': m.helper.REVIEWER,
                   'run_id': f['run']['id'], 'core_state': f['p']['core_state'], 'source_root': f['p']['repo'],
                   'source_sha256': digest(f['repo']), 'controller_package_sha256': f['run']['workflow']}
        path = f['root']/'review.json'
        path.write_bytes(encoded(receipt))
        target = f['root']/'runtime/accepted'
        shutil.copytree(f['repo']/'agent_workflow', target/'agent_workflow')
        assert f['flow'].bridge.finish(review['intent']['request_id'], str(path), sha(path.read_bytes()),
                                       str(target), f['version']['id'])['phase'] == 'completed'
        tid = cases.q.task(f, 'successor')
        cases.q.assessment(f, f['run']['id'], [cases.q.candidate(tid, finding='b'*64)])
        f['clock'][0] += 6
        reopen(f)
        successor = f['flow'].run()
        assert successor['new_dispatches'] == 1 and successor['intent']['role'] == 'worker'
        assert successor['intent']['packet']['task_id'] == tid
        resources(f, 4)
        assert len(f['adapter'].starts()) == 3
        f['c'].stop('fixture complete')
        results.append('real Core worker/review, explicit independent fixture receipt/promotion, authorized successor; restart preserves original ledger')

        for state in ('uncertain', 'expired', 'failed', 'cancelled'):
            f = setup('terminal-'+state, state=state)
            first = f['flow'].run()
            for _ in range(2):
                reopen(f)
                due(f)
                assert f['flow'].run()['new_dispatches'] == 0
            assert len(f['adapter'].starts()) == 1
            resources(f, 2)
            f['c'].stop('fixture complete')
        results.append('unknown/expired/failed/cancelled never redispatched or refunded across restarts')

        f = setup('lost-reply', lost=True)
        denied(lambda: f['flow'].run())
        reopen(f)
        assert f['flow'].run('reconcile')['phase'] == 'awaiting_core_or_receipt'
        assert len(f['adapter'].starts()) == 1
        resources(f, 2)
        f['c'].stop('fixture complete')
        results.append('lost start reply reconciles exact durable intent, no second start')

        for mode in ('cancel', 'deadline'):
            f = setup(mode, state='uncertain')
            f['flow'].run()
            if mode == 'deadline':
                f['clock'][0] = f['original']['deadline']+1
            result = f['flow'].run('cancel' if mode == 'cancel' else 'tick')
            assert result['phase'] == 'stopped' and result['host']['state'] == 'cancelled'
            reopen(f)
            assert f['flow'].run()['new_dispatches'] == 0
            resources(f, 2)
            assert len(f['adapter'].starts()) == 1
        results.append('cancel and original deadline bypass backoff, request cancellation and block new starts')

        for mode in ('cap', 'role'):
            f = setup(mode, cap=1 if mode == 'cap' else 4, roles=['worker'])
            first = f['flow'].run()
            result = worker_ready(f, first)
            assert result['phase'] == ('intent_ceiling' if mode == 'cap' else 'role_not_authorized')
            resources(f, 2)
            assert len(f['adapter'].starts()) == 1
            f['c'].stop('fixture complete')
        results.append('separate intent ceiling and explicit review role required; pair reservation unchanged')

        f = setup('original-budget', cap=1, slots=1)
        assert f['flow'].run()['new_dispatches'] == 0
        assert not f['adapter'].starts()
        resources(f, 0)
        f['c'].stop('fixture complete')
        f = setup('stale-quality')
        cases.q.assessment(f, f['proof'], [cases.q.candidate(f['tid'])], created_at=time.time()-301)
        assert f['flow'].run()['new_dispatches'] == 0
        assert not f['adapter'].starts()
        f['c'].stop('fixture complete')
        results.append('original request budget and stale quality admission refuse backend dispatch')

        f = setup('consumed-no-intent')
        ticket = f['flow'].bridge.reserve()
        f['flow'].bridge.begin(ticket['request_id'])
        denied(lambda: f['flow'].run())
        assert not f['adapter'].starts()
        assert not f['flow']._intents()
        resources(f, 2)
        f['c'].stop('fixture complete')
        results.append('crash gap after bridge begin but before host intent fails closed without re-dispatch')

        f = setup('busy-cancel')
        with f['flow']._serial():
            denied(lambda: f['flow'].run('cancel'))
        assert f['c'].status()['stopped']
        assert not f['adapter'].starts()
        resources(f, 0)
        results.append('cancel persists stop before busy lock refusal; no first dispatch')

        f = setup('cancel-during-start')
        entered = threading.Event()
        release = threading.Event()
        stopped = threading.Event()
        errors = []

        def delayed_start(intent):
            entered.set()
            assert release.wait(10), 'blocked start fixture did not drain'
            # No process is launched; missing execution remains uncertain.
            return {k: intent[k] for k in ('execution_id', 'dispatch_key')} | {
                'state': 'uncertain', 'sequence': 0, 'observed_at': time.time()}

        def ticking():
            try:
                f['flow'].run()
            except BaseException as error:
                errors.append(str(error))

        def cancelling():
            try:
                f['flow'].run('cancel')
            except ValueError as error:
                if 'busy' not in str(error):
                    errors.append(str(error))
            finally:
                stopped.set()

        f['adapter'].start = delayed_start
        worker = threading.Thread(target=ticking)
        canceller = threading.Thread(target=cancelling)
        worker.start()
        try:
            assert entered.wait(10), 'start boundary not reached'
            canceller.start()
            prompt = stopped.wait(2)
        finally:
            release.set()
            worker.join(10)
            if canceller.ident is not None:
                canceller.join(10)
        assert not worker.is_alive() and not canceller.is_alive()
        assert prompt and not errors, 'slow adapter start prevented persistent cancellation: '+str(errors)
        assert f['c'].status()['stopped']
        resources(f, 2)
        results.append('cancellation persists while adapter start is blocked; consumed intent stays uncertain')

        f = setup('oneshot', create_flow=False)
        # A separately initiated old OneShot keeps its original ceiling; Flow
        # cannot silently adopt an existing one-shot execution under a new grant.
        OneShot(f['c'], f['adapter']).run()
        denied(lambda: Flow(f['c'], f['adapter'], str(f['flow_path']), f['flow_sha']))
        f['c'].stop('fixture complete')
        results.append('existing one-shot intent cannot be adopted by flow grant')

        f = setup('local-process', real=True)
        first = f['flow'].run()
        cutoff = time.time()+10
        while time.time() < cutoff:
            observation = f['flow'].run('observe')
            if observation['host']['state'] == 'completed':
                break
            time.sleep(.05)
        assert observation['host']['state'] == 'completed'
        review = worker_ready(f, first)
        assert review['intent']['role'] == 'review'
        cutoff = time.time()+10
        while time.time() < cutoff:
            observation = f['flow'].run('observe')
            if observation['host']['state'] == 'completed':
                break
            time.sleep(.05)
        assert observation['host']['state'] == 'completed'
        assert len((f['work']/'starts.txt').read_text().splitlines()) == 2
        code = 'import sys;sys.path.insert(0,sys.argv.pop(1));from agent_workflow.hostflow import main;main()'
        command = [sys.executable, '-I', '-B', '-c', code, str(SUBJECT), '--state', str(f['continuation']),
                   '--flow-policy', str(f['flow_path']), '--flow-policy-sha256', f['flow_sha'],
                   '--host-policy', str(f['host_policy_path']), '--host-policy-sha256', f['host_policy_sha'], 'observe']
        observed = subprocess.run(command, capture_output=True, text=True, timeout=10,
                                  env={'PATH': os.defpath, 'LANG': 'C.UTF-8'})
        assert observed.returncode == 0, observed.stdout+observed.stderr
        assert json.loads(observed.stdout)['result']['host']['state'] == 'completed'
        resources(f, 2)
        f['flow'].run('cancel')
        results.append('real LocalProcess guardian executes two no-model fixtures for worker/review under original pair')
        f = setup('real-timeout', real=True, sleep=3, process_seconds=1)
        f['flow'].run()
        cutoff = time.time()+10
        while time.time() < cutoff:
            observation = f['flow'].run('observe')
            if observation['host']['state'] == 'expired':
                break
            time.sleep(.05)
        assert observation['host']['state'] == 'expired'
        due(f)
        assert f['flow'].run()['new_dispatches'] == 0
        assert len((f['work']/'starts.txt').read_text().splitlines()) == 1
        resources(f, 2)
        f['c'].stop('fixture complete')
        results.append('real guardian hard timeout does not redispatch or replenish original pair')
    print(json.dumps({'passed': True, 'installed': A.installed, 'cases': results,
                      'real_core': True, 'simulated_review_receipts': True,
                      'external_models': 0, 'wake_source_installed': False}))


if __name__ == '__main__':
    main()
