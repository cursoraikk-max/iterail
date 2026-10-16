#!/usr/bin/env python3
"""Runner admission contracts; reserve only, never launch a backend/model."""
import argparse
import json
from pathlib import Path
import subprocess
import selectors
import sys
import tempfile
from unittest.mock import patch

P = argparse.ArgumentParser()
P.add_argument('--installed', action='store_true')
P.add_argument('--child')
A = P.parse_args()
ROOT = Path(__file__).resolve().parents[2]
if not A.installed:
    sys.path.insert(0, str(ROOT))
from agent_workflow.runner import Runner, RunnerError


def child(state):
    r = Runner(state)
    try:
        print('ready', flush=True)
        assert sys.stdin.readline() == 'go\n'
        try:
            r.reserve(None, 'backend:worker')
            outcome = 'reserved'
        except RunnerError as error:
            assert 'reviewer' in str(error), str(error)
            outcome = 'denied'
        print(json.dumps({'outcome': outcome}), flush=True)
    finally:
        r.db.close()


def git(repo, *args):
    subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True)


def fixture(root, name):
    repo = root / name / 'repo'
    repo.mkdir(parents=True)
    git(repo, 'init', '-q')
    (repo / 'data.txt').write_text('owned native fixture\n')
    git(repo, 'add', '.')
    git(repo, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
        '-c', 'commit.gpgsign=false', 'commit', '-qm', 'fixture')
    check = repo.parent / 'check.py'
    check.write_text('raise SystemExit(0)\n')
    config = {'repo': str(repo), 'backend': sys.executable,
              'checks': [{'id': 'fixture', 'argv': [sys.executable, str(check)], 'repair': 'inert'}],
              'tests': [[sys.executable, str(check)]],
              'budget': {'wall_seconds': 400, 'max_calls': 2, 'call_seconds': 2,
                         'log_bytes': 1024, 'max_attempts': 1},
              'allowlist': ['docs/*.md'], 'poll_seconds': 1}
    return Runner(repo.parent / 'state', config)


def race(r):
    argv = [sys.executable, '-I', '-B', str(Path(__file__).resolve()), '--child', str(r.root)]
    if A.installed:
        argv.append('--installed')
    processes = [subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True) for _ in range(2)]
    try:
        # Both processes have opened the same initialized ledger before release.
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
    with tempfile.TemporaryDirectory(prefix='iterail-runner-budget-') as temp:
        root = Path(temp)
        for delta in (-0.125, 0, 0.125):
            r = fixture(root, 'wall-' + str(delta))
            try:
                before = r.status()
                started = before['meta']['started']
                # Independent constant for this fixture: worker2 + reviewer2 +
                # two machine gates4 + capped Core bundle2 + four bookkeeping ops8.
                required = 18
                with patch('agent_workflow.runner.time.time', return_value=started + 400 - required - delta):
                    if delta < 0:
                        try:
                            r.reserve(None, 'backend:worker')
                        except RunnerError as error:
                            assert 'wall' in str(error)
                        else:
                            raise AssertionError('insufficient wall admitted')
                        assert r.status() == before, 'denied admission changed ledger'
                    else:
                        r.reserve(None, 'backend:worker')
                        assert r.status()['meta']['calls'] == 1
                cases.append('wall threshold ' + str(delta))
            finally:
                r.db.close()
        for remaining in (0, 1):
            r = fixture(root, 'slots-' + str(remaining))
            try:
                for _ in range(2 - remaining):
                    r.reserve(None, 'backend:reviewer')
                before = r.status()
                try:
                    r.reserve(None, 'backend:worker')
                except RunnerError:
                    pass
                else:
                    raise AssertionError('worker consumed reviewer allowance')
                assert r.status() == before
                reopened = Runner(r.root)
                try:
                    assert reopened.status() == before, 'reopen reset ledger'
                finally:
                    reopened.db.close()
                cases.append('remaining calls ' + str(remaining))
            finally:
                r.db.close()
        r = fixture(root, 'stopped')
        try:
            r.set_stop(True)
            before = r.status()
            try:
                r.reserve(None, 'backend:reviewer')
            except RunnerError:
                pass
            else:
                raise AssertionError('stopped ledger admitted')
            assert r.status() == before
            cases.append('persistent stop rejects without charge')
        finally:
            r.db.close()
        r = fixture(root, 'race')
        try:
            race(r)
            state = r.status()
            assert state['meta']['calls'] == 1 and len(state['calls']) == 1
            assert len([e for e in state['events'] if e['kind'] == 'call_reserved']) == 1
            r.reserve(None, 'backend:reviewer')
            assert r.status()['meta']['calls'] == 2
            cases.append('two processes compete; last reviewer slot preserved')
        finally:
            r.db.close()
    print(json.dumps({'passed': True, 'cases': cases, 'installed': A.installed,
                      'backend_launches': 0, 'model_calls': 0}))


if __name__ == '__main__':
    child(A.child) if A.child else main()
