import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import unittest
from unittest import mock

from agent_workflow.core import Engine
from tests import test_core


@unittest.skipUnless(os.name == 'posix', 'POSIX process group guardian')
class GuardianTests(unittest.TestCase):
    setUp = test_core.CoreTests.setUp

    def test_parent_death_before_gate_release_never_executes_command(self):
        runtime = self.repo / '.workflow'
        runtime.mkdir()
        ready, executed = runtime / 'gate-ready', runtime / 'executed'
        code = "from pathlib import Path; Path(%r).write_text('executed')" % str(executed)
        helper = f"""
import time
from pathlib import Path
from agent_workflow import core_process
from tests.test_core import config
from agent_workflow.core import Engine
original = core_process.subprocess.Popen
ready = Path({str(ready)!r})
def popen(*args, **kwargs):
    p = original(*args, **kwargs)
    if len(args[0]) > 2 and args[0][2] == 'gate':
        ready.write_text(str(p.pid))
    return p
core_process.subprocess.Popen = popen
def block(fd):
    ready.write_text(ready.read_text() + ' ready')
    time.sleep(30)
core_process._open_gate = block
c = config(Path({str(self.repo)!r}))
c['tests'] = [{[sys.executable, '-c', code]!r}]
e = Engine({str(self.root / 'gate-state')!r}, c)
t = e.import_task('gate', 'Gate')['id']
r = e.claim('gate', t)
e.change(r['id'], r['token'])
e.verify(r['id'], r['token'])
"""
        process = subprocess.Popen([sys.executable, '-c', helper], stdout=subprocess.DEVNULL,
                                   stderr=subprocess.PIPE)
        group = None
        try:
            until = time.monotonic() + 4
            while time.monotonic() < until:
                if ready.exists() and ready.read_text().endswith(' ready'):
                    break
                if process.poll() is not None:
                    self.fail('gate helper exited: ' + process.stderr.read().decode())
                time.sleep(.02)
            self.assertTrue(ready.exists() and ready.read_text().endswith(' ready'))
            group = int(ready.read_text().split()[0])
            process.kill()
            process.wait(timeout=2)
            time.sleep(.35)
            self.assertFalse(executed.exists(), 'approved command ran before release')
            # Gate readiness was observed after guardian ACK, before release.
            # The real command must remain unexecuted after parent pipe EOF.
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=2)
            process.stderr.close()
            if group:
                try:
                    os.killpg(group, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def test_missing_executable_retains_launch_error(self):
        c = test_core.config(self.repo)
        c['tests'] = [[str(self.root / 'no-such-executable')]]
        e = Engine(self.root / 'missing-exe-state', c)
        self.addCleanup(e.db.close)
        tid = e.import_task('missing', 'Missing')['id']
        r = e.claim('missing-test', tid)
        args = {'run_id': r['id'], 'token': r['token']}
        e.change(**args)
        result = e.verify(**args)
        self.assertFalse(result['passed'])
        command = result['evidence']['results'][0]
        self.assertEqual(command['exit_code'], 127)
        self.assertTrue(command['execution_error'])
        self.assertIn('FileNotFoundError', command['internal_error'])
        self.assertEqual(e.status(run_id=r['id'])['state'], 'changed')

    def test_embedded_null_argv_retains_launch_value_error(self):
        c = test_core.config(self.repo)
        c['tests'] = [['missing\0name']]
        e = Engine(self.root / 'nul-exe-state', c)
        self.addCleanup(e.db.close)
        tid = e.import_task('nul', 'Nul argv')['id']
        r = e.claim('nul-test', tid)
        args = {'run_id': r['id'], 'token': r['token']}
        e.change(**args)
        result = e.verify(**args)
        self.assertFalse(result['passed'])
        command = result['evidence']['results'][0]
        self.assertEqual(command['exit_code'], 127)
        self.assertTrue(command['execution_error'])
        self.assertIn('ValueError', command['internal_error'])

    def test_secondary_cleanup_error_never_masks_primary_launch_failure(self):
        c = test_core.config(self.repo)
        c['tests'] = [[str(self.root / 'missing-with-cleanup-error')]]
        e = Engine(self.root / 'cleanup-error-state', c)
        self.addCleanup(e.db.close)
        tid = e.import_task('cleanup', 'Cleanup fault')['id']
        r = e.claim('cleanup-test', tid)
        args = {'run_id': r['id'], 'token': r['token']}
        e.change(**args)
        # Parent-only injected OS cleanup error; guardian subprocess remains real.
        with mock.patch('agent_workflow.core_process._kill_group',
                        side_effect=PermissionError(1, 'injected cleanup permission error')):
            result = e.verify(**args)
        self.assertFalse(result['passed'])
        command = result['evidence']['results'][0]
        self.assertEqual(command['exit_code'], 127)
        self.assertTrue(command['execution_error'])
        self.assertIn('FileNotFoundError', command['internal_error'])
        self.assertIn('PermissionError', command['cleanup_error'])
        self.assertEqual(e.status(run_id=r['id'])['state'], 'changed')

    def test_timeout_cleanup_error_preserves_timeout_exit(self):
        c = test_core.config(self.repo)
        c['budget']['test_seconds'] = 1
        c['tests'] = [[sys.executable, '-c', 'import time; print("before",flush=True); time.sleep(3)']]
        e = Engine(self.root / 'timeout-cleanup-state', c)
        self.addCleanup(e.db.close)
        tid = e.import_task('timeout-cleanup', 'Timeout cleanup')['id']
        r = e.claim('timeout-cleanup-test', tid)
        args = {'run_id': r['id'], 'token': r['token']}
        e.change(**args)
        # Actual child/guardian subprocesses stay unpatched. Only parent's
        # cleanup syscall fails, preserving the already established timeout.
        with mock.patch('agent_workflow.core.os.killpg',
                        side_effect=PermissionError(1, 'injected timeout cleanup error')):
            result = e.verify(**args)
        command = result['evidence']['results'][0]
        self.assertFalse(result['passed'])
        self.assertEqual(command['exit_code'], 124)
        self.assertTrue(command['timeout'])
        self.assertTrue(command['execution_error'])
        self.assertIn('PermissionError', command['cleanup_error'])
        self.assertIn('before', command['stdout'])

    def test_verifier_sigkill_stops_ordinary_grandchild(self):
        marker = self.repo / '.workflow' / 'heartbeat'
        groupfile = self.repo / '.workflow' / 'group'
        marker.parent.mkdir()
        child = "from pathlib import Path; import time; p=Path(%r); exec('while True:\\n p.write_text(str(time.monotonic_ns()))\\n time.sleep(.02)')" % str(marker)
        command = "import os,subprocess,sys,time; from pathlib import Path; Path(%r).write_text(str(os.getpgrp())); subprocess.Popen([sys.executable,'-c',%r]); time.sleep(9)" % (str(groupfile), child)
        c = test_core.config(self.repo)
        c['tests'] = [[sys.executable, '-c', command]]
        state = self.root / 'sigkill-state'
        e = Engine(state, c)
        self.addCleanup(e.db.close)
        tid = e.import_task('sigkill', 'SIGKILL')['id']
        r = e.claim('sigkill-test', tid)
        e.change(r['id'], r['token'])
        helper = "from agent_workflow.core import Engine; e=Engine(%r); e.verify(%r,%r)" % (str(state), r['id'], r['token'])
        process = subprocess.Popen([sys.executable, '-c', helper], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        group = None
        try:
            until = time.monotonic() + 4
            while not marker.exists() and time.monotonic() < until:
                time.sleep(.02)
            self.assertTrue(marker.exists(), 'grandchild never started')
            group = int(groupfile.read_text())
            process.kill()
            process.wait(timeout=2)
            time.sleep(.35)
            before = marker.read_text()
            time.sleep(.25)
            self.assertEqual(marker.read_text(), before, 'ordinary descendant survived verifier SIGKILL')
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=2)
            if group is None and groupfile.exists():
                group = int(groupfile.read_text())
            if group:
                try:
                    os.killpg(group, signal.SIGKILL)
                except ProcessLookupError:
                    pass
