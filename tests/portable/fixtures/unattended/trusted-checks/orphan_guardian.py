"""Trusted local source: kill a real verifier and check ordinary descendants stop."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

from common import configuration, emit, fixture_repo, load_core


def helper(repo, root):
    Engine, _ = load_core(repo)
    root = Path(root)
    fixture = root / 'repo'
    heartbeat = "from pathlib import Path; import time; p=Path('.workflow/heartbeat'); " \
                "exec('while True:\\n p.write_text(str(time.monotonic_ns()))\\n time.sleep(.04)')"
    command = "import os,subprocess,sys,time; from pathlib import Path; " \
              "Path('.workflow/group').write_text(str(os.getpgrp())); " \
              "subprocess.Popen([sys.executable,'-c',%r]); time.sleep(9)" % heartbeat
    engine = Engine(root / 'state', configuration(fixture, [sys.executable, '-c', command]))
    try:
        task = engine.import_task('orphan', 'Orphan guardian')['id']
        run = engine.claim('trusted-orphan-check', task)
        args = {'run_id': run['id'], 'token': run['token']}
        engine.change(**args)
        engine.verify(**args)
    finally:
        engine.db.close()


def check(repo):
    _, digest = load_core(repo)
    evidence = {'source_digest': digest(repo), 'stop_window_seconds': 1.0}
    group = None
    process = None
    with tempfile.TemporaryDirectory(prefix='workflow-orphan-check-') as tmp:
        root = Path(tmp)
        fixture = fixture_repo(root)
        marker = fixture / '.workflow/heartbeat'
        groupfile = fixture / '.workflow/group'
        try:
            process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--repo', str(repo),
                                        '--helper', str(root)], stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL)
            until = time.monotonic() + 5
            while time.monotonic() < until and not (marker.exists() and groupfile.exists()):
                if process.poll() is not None:
                    raise RuntimeError('verification helper exited before child ready')
                time.sleep(.02)
            if not marker.exists() or not groupfile.exists():
                raise RuntimeError('child readiness timeout')
            group = int(groupfile.read_text())
            process.kill()
            process.wait(timeout=2)
            killed_at = time.monotonic()
            # A stopped writer must remain unchanged for 200ms before the 1s bound.
            previous = marker.read_text()
            unchanged_since = time.monotonic()
            stopped = False
            while time.monotonic() - killed_at < 1.0:
                time.sleep(.025)
                current = marker.read_text()
                if current != previous:
                    previous = current
                    unchanged_since = time.monotonic()
                elif time.monotonic() - unchanged_since >= .2:
                    stopped = True
                    break
            evidence.update(helper_exit_code=process.returncode, observed_stop=stopped,
                            observation_seconds=time.monotonic()-killed_at, group_pid=group)
            return evidence, stopped
        finally:
            if process is not None and process.poll() is None:
                process.kill()
                process.wait(timeout=2)
            if group is None and groupfile.exists():
                group = int(groupfile.read_text())
            if group is not None:
                try:
                    os.killpg(group, signal.SIGKILL)
                    evidence['cleanup_group_killed'] = True
                except ProcessLookupError:
                    evidence['cleanup_group_absent'] = True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repo', required=True)
    parser.add_argument('--helper', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.helper:
        helper(args.repo, args.helper)
        return 0
    try:
        evidence, passed = check(args.repo)
    except Exception as error:
        evidence, passed = {'error': type(error).__name__ + ': ' + str(error)}, False
    return emit('orphan-guardian', 'Stop test descendants after verifier process death', evidence,
                'Add an independent local watcher using parent-liveness pipe EOF and execution deadline '
                'to kill the approved test process group when the verifier dies. Preserve lease recovery '
                'and never claim OS sandbox isolation.', passed)


if __name__ == '__main__':
    raise SystemExit(main())
