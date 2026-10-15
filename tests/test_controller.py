"""An attempt must be managed by the controller that claimed it."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

from agent_workflow import core
from tests import test_core


class ControllerTests(unittest.TestCase):
    setUp = test_core.CoreTests.setUp
    claim = test_core.CoreTests.claim

    def test_other_controller_cannot_mutate_claimed_attempt(self):
        r, args = self.claim()
        self.e.change(**args)
        controller = self.root / 'other-controller'
        shutil.copytree(Path(core.__file__).parent, controller / 'agent_workflow')
        with (controller / 'agent_workflow' / 'core.py').open('a') as f:
            f.write('\n# Distinct real controller source version.\n')
        env = dict(os.environ, PYTHONPATH=str(controller), PYTHONDONTWRITEBYTECODE='1')
        actions = {
            'heartbeat': {}, 'progress': {'note': 'wrong controller'},
            'change': {}, 'verify': {}, 'transition': {'target': 'ready_review'},
            'fail': {'reason': 'wrong controller'},
        }
        before = self.e.history(self.t)
        for action, extra in actions.items():
            with self.subTest(action=action):
                p = subprocess.run(
                    [sys.executable, '-B', '-m', 'agent_workflow', '--state', str(self.state),
                     action, '--args', json.dumps(dict(args, **extra))],
                    cwd=self.root, env=env, capture_output=True, text=True, check=False)
                response = json.loads(p.stdout)
                self.assertEqual(p.returncode, 1, response)
                self.assertFalse(response['ok'])
                self.assertEqual(response['error'], 'controller version differs from claimed workflow')
                self.assertEqual(self.e.history(self.t), before)
        # The pinned controller still owns and can complete the same attempt.
        self.e.heartbeat(**args)
        self.assertTrue(self.e.verify(**args)['passed'])
        self.e.transition(**args, target='ready_review')
        self.e.transition(**args, target='accepted')
        self.assertEqual(self.e.status(run_id=r['id'])['state'], 'accepted')
