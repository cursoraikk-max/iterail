#!/usr/bin/env python3
"""Actual local completion gates; no platform stop, model, or backend launch.

Owner acceptance and quality assessments below are explicitly native fixtures.
They do not represent an independent AI review or real project completion.
"""
import argparse
import ast
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

parser = argparse.ArgumentParser()
parser.add_argument('--installed', action='store_true')
args = parser.parse_args()
ROOT = Path(__file__).resolve().parents[2]
if not args.installed:
    sys.path.insert(0, str(ROOT))
import agent_workflow
from agent_workflow.continuation import Continuation, digest, sha, encoded
from agent_workflow.core import Engine, WorkflowError
from agent_workflow.hostbridge import HostBridge
from agent_workflow.hostsupervisor import OneShot
from agent_workflow.hostflow import Flow


class NoDispatch:
    """Observation-only spy: any start/cancel in these no-process cases fails."""
    identity = 'completion-contract-native-spy-v1'

    def __init__(self, work):
        self.workdir = str(work)
        self.starts = 0

    def start(self, intent):
        self.starts += 1
        raise AssertionError('terminal owner attempted a new dispatch')

    def observe(self, intent):
        raise AssertionError('no host execution exists in this fixture')

    reconcile = observe
    cancel = observe


def rejected(fn, message):
    try:
        fn()
    except (ValueError, WorkflowError) as error:
        assert message in str(error), str(error)
    else:
        raise AssertionError('operation should be fenced: '+message)


def counts(c):
    return {table: c._core_read('SELECT count(*) AS n FROM '+table)[0]['n']
            for table in ('tasks', 'runs')}


def same_resources(before, after):
    for key in ('started', 'deadline', 'requests_spent'):
        assert before[key] == after[key], key


def setup(root):
    # Extract only the existing fixture definitions; do not run its test program.
    tree = ast.parse((ROOT/'tests/packaging/hostbridge_recovery.py').read_text())
    nodes = [n for n in tree.body if
             isinstance(n, ast.FunctionDef) and n.name == 'fixture' or
             isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'CHECK' for t in n.targets)]

    class TestOwnerBudget(ast.NodeTransformer):
        def visit_Dict(self, node):
            self.generic_visit(node)
            for i, key in enumerate(node.keys):
                if isinstance(key, ast.Constant) and key.value in {'lease_seconds', 'run_seconds'}:
                    node.values[i] = ast.Constant(120)
            return node

    module = ast.fix_missing_locations(TestOwnerBudget().visit(ast.Module(body=nodes, type_ignores=[])))
    scope = dict(globals(), PACKAGE=Path(agent_workflow.__file__).parent)
    exec(compile(module, 'existing-owner-fixture', 'exec'), scope)
    return scope['fixture'](root, 'accepted', claim=False, bind=False)


def main():
    checks = []
    with tempfile.TemporaryDirectory(prefix='iterail-completion-contract-') as temp:
        root = Path(temp).resolve()
        f = setup(root)
        c = f['c']
        version = c.status()['active_version']
        run = c._core('claim', {'worker': f['t']['actor'], 'task_id': f['t']['task_id']}, version)
        owned = {'run_id': run['id'], 'token': run['token']}
        c.bind_run(f['t']['request_id'], run['id'])
        (f['repo']/'value.txt').write_text('fixed\n')
        c._core('change', owned, version)
        assert c._core('verify', owned, version)['passed']
        c._core('transition', dict(owned, target='ready_review'), version)
        # Legacy Core machine acceptance; scope/review decisions are fixture data.
        c._core('transition', dict(owned, target='accepted'), version)
        advice = c._core('next_step', {'run_id': run['id']}, version)
        assert advice['next_action'] == 'done' and advice['evidence_valid']
        assert advice['source_match'] and advice['published'] is False
        assert c.tick()['phase'] == 'awaiting_promotion'
        core = Engine(f['home']/'core')
        try:
            rejected(lambda: core.claim('repeat-host', run['task_id']), 'no queued task')
        finally:
            core.db.close()
        before = c.status()
        original_counts = counts(c)
        checks.append('current Core acceptance is task proof; promotion remains separate; accepted task cannot be reclaimed')

        value = f['repo']/'value.txt'
        original = value.read_bytes()
        value.write_text('stale historical artifact\n')
        stale = c._core('next_step', {'run_id': run['id']}, version)
        assert stale['next_action'] == 'owner_reassess' and not stale['evidence_valid']
        value.write_bytes(original)
        assert c._core('next_step', {'run_id': run['id']}, version)['next_action'] == 'done'
        checks.append('stale source cannot reuse accepted success; exact restoration is required')

        # Host owner records completion stop only after its own scope decision.
        c.stop('native fixture: finite owner scope complete; host termination unconfirmed')
        c.db.close()
        c = Continuation(str(f['home']/'continuation'))
        adapter = NoDispatch(root)
        pp = root/'flow.json'
        policy = {'schema_version': 1, 'continuation_state': str(c.root),
                  'continuation_identity_sha256': sha((c.root/'identity.json').read_bytes()),
                  'adapter_identity': adapter.identity, 'allowed_roles': ['worker', 'review'],
                  'max_intents': 4, 'poll_seconds': 1}
        pp.write_bytes(encoded(policy))
        flow = Flow(c, adapter, str(pp), sha(pp.read_bytes()))
        for _ in range(3):
            assert c.resume()['stopped']
            assert c.tick()['phase'] == 'stopped'
            rejected(lambda: HostBridge(c).reserve(), 'stopped or expired')
            rejected(lambda: OneShot(c, adapter).run(), 'stopped or expired')
            result = flow.run()
            assert result['phase'] == 'stopped' and result['new_dispatches'] == 0
            assert flow.run('reconcile')['new_dispatches'] == 0
            same_resources(before, c.status())
            assert counts(c) == original_counts
            assert not flow._intents() and adapter.starts == 0
        assert c.status()['request'] == before['request']
        c.db.close()
        checks.append('completion stop survives reopen/repeated ticks/resume: zero new task/run/intent/adapter start, original budget/pending retained')

        # Existing quality fixtures explicitly simulate assessments and owner time.
        spec = importlib.util.spec_from_file_location('completion_quality_fixture', ROOT/'tests/portable/fixtures/research-trial/trusted/continuation_quality_guard.py')
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        cases = m.Cases(Path(agent_workflow.__file__).parent.parent, root/'quality')
        (root/'quality').mkdir()
        paused = cases.fixture('pause')
        proof = cases.q.baseline(paused)
        cases.q.assessment(paused, proof)
        pause = paused['c'].tick()
        assert pause['phase'] == 'stop_no_value' and not pause['stopped'] and pause['request'] is None
        assert pause['requests_spent'] == 0
        paused['c'].stop('native fixture: human decision required, not project success')
        cases.reopen(paused)
        blocked = paused['c'].tick()
        assert blocked['stopped'] and blocked['phase'] == 'stopped'
        same_resources(pause, blocked)
        assert paused['c'].resume()['stopped']
        paused['c'].db.close();paused['e'].db.close()
        checks.append('no-value pause is not hard stop; explicit human block persists without completion or renewed budget')

        expired = cases.fixture('deadline')
        old = expired['c'].status()
        expired['clock'][0] = old['deadline'] + 1  # Explicit native clock simulation.
        cases.reopen(expired)
        assert expired['c'].tick()['phase'] == 'blocked'
        rejected(lambda: HostBridge(expired['c']).reserve(), 'stopped or expired')
        same_resources(old, expired['c'].status())
        assert expired['c'].status()['request'] is None
        expired['c'].db.close();expired['e'].db.close()
        checks.append('simulated owner deadline blocks reservation after reopen without refreshing grant')
    print(json.dumps({'passed': True, 'checks': checks, 'adapter_start_attempts': 0,
                      'host_stop_calls': 0, 'external_model_calls': 0,
                      'quality_assessments': 'native owner-authored fixtures',
                      'deadline_clock': 'explicit simulation', 'platform_stop_verified': False}))


if __name__ == '__main__':
    main()
