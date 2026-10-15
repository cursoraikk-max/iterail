import concurrent.futures
import json
from pathlib import Path
import sys
import time
import unittest

from agent_workflow.core import Engine, WorkflowError
from tests import test_core


class VerificationExpiryTests(unittest.TestCase):
    setUp = test_core.CoreTests.setUp
    claim = test_core.CoreTests.claim

    def test_expired_verification_retains_output_without_overwriting_recovery(self):
        self.e.db.close()
        self.state = self.root / 'expiry-state'
        marker = self.root / 'descendant-marker'
        second = self.root / 'second-marker'
        child = ('import time; from pathlib import Path; time.sleep(1.4); '
                 'Path(' + repr(str(marker)) + ').write_text("survived")')
        code = ('import subprocess,sys,time; print("before-expiry",flush=True); '
                'subprocess.Popen([sys.executable,"-c",' + repr(child) + ']); '
                'time.sleep(1.2); print("after-expiry",flush=True)')
        self.c['budget'].update(lease_seconds=1, run_seconds=10, test_seconds=3)
        self.c['tests'] = [[sys.executable, '-c', code], [sys.executable, '-c',
            'from pathlib import Path; Path(' + repr(str(second)) + ').write_text("ran")']]
        self.e = Engine(self.state, self.c)
        self.t = self.e.import_task('expiry', 'Expiry evidence')['id']
        r, args = self.claim()
        self.e.change(**args)

        def verify_in_other_connection():
            e = Engine(self.state)
            try:
                return e.verify(**args)
            finally:
                e.db.close()

        with concurrent.futures.ThreadPoolExecutor(1) as pool:
            pending = pool.submit(verify_in_other_connection)
            wait_until = time.time() + .5
            while self.e.status(run_id=r['id'])['state'] != 'verifying' and time.time() < wait_until:
                time.sleep(.005)
            self.assertEqual(self.e.status(run_id=r['id'])['state'], 'verifying')
            for action, extra in [(self.e.verify, {}), (self.e.change, {}),
                                  (self.e.heartbeat, {}), (self.e.fail, {'reason': 'interrupt'})]:
                with self.assertRaisesRegex(WorkflowError, 'verification in progress'):
                    action(**args, **extra)
            time.sleep(max(0, r['lease_until'] - time.time() + .02))
            successor = self.e.claim('recovery', self.t)
            result = pending.result(timeout=5)
        self.assertFalse(result['passed'])
        output = result['evidence']['results'][0]
        self.assertEqual(output['exit_code'], 124)
        self.assertIn('before-expiry', output['stdout'])
        self.assertNotIn('after-expiry', output['stdout'])
        self.assertFalse(second.exists())
        time.sleep(.5)
        self.assertFalse(marker.exists(), 'verification descendant survived lease expiry')
        old = self.e.status(run_id=r['id'])
        self.assertEqual(old['state'], 'expired')
        self.assertEqual(json.loads(old['evidence']), result['evidence'])
        self.assertEqual(self.e.status(run_id=successor['id'])['state'], 'running')
        self.assertEqual(self.e.status(task_id=self.t)['state'], 'running')
        events = self.e.history(self.t)['events']
        self.assertTrue(any(x['run_id'] == r['id'] and 'verification' in x['kind'] for x in events))
        for action, extra in [(self.e.verify, {}), (self.e.transition, {'target': 'ready_review'})]:
            with self.assertRaises(WorkflowError):
                action(**args, **extra)
