#!/usr/bin/env python3
"""Durable Governor accounting; native fixtures, no backend/model execution."""
import argparse
import importlib.util
import json
from pathlib import Path
import selectors
import subprocess
import sys

P = argparse.ArgumentParser()
P.add_argument('--installed', action='store_true')
P.add_argument('--child')
A = P.parse_args()
ROOT = Path(__file__).resolve().parents[2]
if not A.installed:
    sys.path.insert(0, str(ROOT))
from agent_workflow.evolution import Governor, EvolutionError


def child(state):
    g = Governor(state)
    try:
        print('ready', flush=True)
        assert sys.stdin.readline() == 'go\n'
        try:
            g.reserve('attempt', seconds=2, job='race')
            outcome = 'reserved'
        except EvolutionError as error:
            assert 'budget' in str(error), str(error)
            outcome = 'denied'
        print(json.dumps({'outcome': outcome}), flush=True)
    finally:
        g.db.close()


def fixture():
    # Reuse immutable local setup only; Governor imports remain source/installed
    # as selected above. This helper never dispatches its deliberately inert backend.
    path = ROOT / 'tests/portable/fixtures/phase4/trusted/common.py'
    spec = importlib.util.spec_from_file_location('accounting_setup', path)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    helper.REPO = ROOT
    temp, root, config = helper.fixture(None, max_calls=2, wall=400)
    try:
        return temp, Governor(root / 'owner', config)
    except BaseException:
        temp.cleanup()
        raise


def denied_unchanged(g, operation):
    before = g.status()
    try:
        operation()
    except EvolutionError:
        pass
    else:
        raise AssertionError('invalid accounting operation accepted')
    assert g.status() == before, 'failed operation partially changed owner ledger'


def race(g):
    argv = [sys.executable, '-I', '-B', str(Path(__file__).resolve()), '--child', str(g.root)]
    if A.installed:
        argv.append('--installed')
    processes = [subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True) for _ in range(2)]
    try:
        for process in processes:
            with selectors.DefaultSelector() as ready:
                ready.register(process.stdout, selectors.EVENT_READ)
                assert ready.select(timeout=20), 'child readiness timeout'
            assert process.stdout.readline().strip() == 'ready'
        for process in processes:
            process.stdin.write('go\n')
            process.stdin.flush()
        outcomes = []
        for process in processes:
            out, err = process.communicate(timeout=20)
            assert process.returncode == 0, err
            outcomes.append(json.loads(out)['outcome'])
        assert sorted(outcomes) == ['denied', 'reserved'], outcomes
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)


def main():
    cases = []
    temp, g = fixture()
    try:
        original = g.status()['meta']
        ident, _ = g.reserve('attempt', seconds=2, job='native-accounting')
        reserved = g.status()
        assert reserved['meta']['calls'] == 2
        assert reserved['accounting']['unknown_reviewer_reservations'] == 1
        assert reserved['accounting']['successful_model_turns'] == 0
        assert not reserved['accounting']['recovery']['reservation_refund_allowed']
        denied_unchanged(g, lambda: g.finish(ident, {'actual_used': 3}))
        g.finish(ident, {'actual_used': 1, 'backend_kinds': ['backend:worker']})
        finished = g.status()
        assert finished['accounting']['unexecuted_reviewer_reservations'] == 1
        assert finished['meta']['calls'] == 2, 'unused reviewer refunded charged pair'
        for key in ('started', 'deadline'):
            assert finished['meta'][key] == original[key]
        for key in ('calls', 'reserved_seconds', 'reserved_bytes'):
            assert finished['meta'][key] == reserved['meta'][key]
        g.finish(ident, {'actual_used': 1, 'backend_kinds': ['backend:worker']})
        assert g.status() == finished, 'same completion added events or changed accounting'
        g.finish(ident, {'actual_used': 0, 'backend_kinds': ['backend:worker']})
        assert g.status() == finished, 'replayed lower usage reset charged observations'
        denied_unchanged(g, lambda: g.finish(ident, {'exit_code': 1}))
        denied_unchanged(g, lambda: g.reserve('attempt', seconds=2))
        reopened = Governor(g.root)
        try:
            assert reopened.status() == finished, 'restart reset durable accounting'
        finally:
            reopened.db.close()
        cases.append('pair charge, overuse refusal, finish replay/rebind and restart')
    finally:
        g.db.close()
        temp.cleanup()
    temp, g = fixture()
    try:
        # Reservation metadata alone does not prove that a process/model ran.
        ident, _ = g.reserve('supervised:backend:reviewer', seconds=2)
        g.finish(ident, {'terminal': {'call_id': 'call-' + 'a' * 12}})
        before = g.status()
        accounting = before['accounting']
        assert accounting['unknown_backend_launches'] == 1
        assert accounting['unknown_model_turns'] == 1
        assert accounting['actual_backend_launches'] == 0
        assert accounting['successful_model_turns'] == 0
        assert g.status() == before, 'status observation mutated canonical ledger'
        cases.append('incomplete terminal remains unknown; status is read only')
    finally:
        g.db.close()
        temp.cleanup()
    temp, g = fixture()
    try:
        original = g.status()['meta']
        race(g)
        state = g.status()
        assert state['meta']['calls'] == 2
        assert len(state['reservations']) == 1
        assert len([e for e in state['events'] if e['kind'] == 'reserved']) == 1
        assert state['meta']['started'] == original['started']
        assert state['meta']['deadline'] == original['deadline']
        denied_unchanged(g, lambda: g.reserve('attempt', seconds=2))
        cases.append('two real processes compete for final pair; no overspend')
    finally:
        g.db.close()
        temp.cleanup()
    print(json.dumps({'passed': True, 'cases': cases, 'installed': A.installed,
                      'backend_launches': 0, 'model_calls': 0}))


if __name__ == '__main__':
    child(A.child) if A.child else main()
