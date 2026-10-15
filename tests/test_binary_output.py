import base64
import unittest
from unittest import mock

from tests import test_core
from tests.test_core import config
from agent_workflow.core import Engine
import sys


class BinaryOutputTests(unittest.TestCase):
    setUp = test_core.CoreTests.setUp
    claim = test_core.CoreTests.claim
    def test_arbitrary_stdout_stderr_bytes_preserve_exit_code(self):
        for exit_code in (0, 7):
            with self.subTest(exit_code=exit_code):
                c = config(self.repo)
                c['tests'] = [[sys.executable, '-c',
                    "import os; os.write(1,b'\\xff\\x00\\x80\\xc3('); "
                    "os.write(2,b'\\xfe\\x81\\n'); raise SystemExit(%d)" % exit_code]]
                e = Engine(self.root / ('binary-%d' % exit_code), c)
                self.addCleanup(e.db.close)
                task = e.import_task('binary', 'Binary')['id']
                r = e.claim('binary-worker', task)
                args = {'run_id': r['id'], 'token': r['token']}
                e.change(**args)
                result = e.verify(**args)
                self.assertEqual(result['passed'], exit_code == 0)
                command = result['evidence']['results'][0]
                self.assertEqual(command['exit_code'], exit_code)
                self.assertEqual(base64.b64decode(command['stdout_base64']), b'\xff\x00\x80\xc3(')
                self.assertEqual(base64.b64decode(command['stderr_base64']), b'\xfe\x81\n')
                self.assertEqual(command['output_encoding'], 'utf-8')
                self.assertEqual(command['output_errors'], 'replace')
                self.assertIn('\ufffd', command['stdout'])
                self.assertIn('\ufffd', command['stderr'])
                self.assertEqual(e.status(run_id=r['id'])['state'], 'verified' if exit_code == 0 else 'changed')

    def test_unexpected_capture_exception_records_failed_evidence(self):
        r, args = self.claim()
        self.e.change(**args)
        with mock.patch('agent_workflow.core.subprocess.Popen.communicate',
                        side_effect=RuntimeError('unexpected capture failure')):
            result = self.e.verify(**args)
        self.assertFalse(result['passed'])
        command = result['evidence']['results'][0]
        self.assertTrue(command['execution_error'])
        self.assertIn('RuntimeError', command['stderr'])
        self.assertEqual(self.e.status(run_id=r['id'])['state'], 'changed')
        self.assertEqual(self.e.history(self.t)['events'][-1]['kind'], 'verification_failed')

    def test_post_execution_source_check_error_records_failure(self):
        c = config(self.repo)
        c['tests'] = [[sys.executable, '-c',
                       "from pathlib import Path; Path('unsupported-link').symlink_to('code.py'); print('done')"]]
        e = Engine(self.root / 'source-check-error', c)
        self.addCleanup(e.db.close)
        task = e.import_task('source-check', 'Source check')['id']
        r = e.claim('source-check-worker', task)
        args = {'run_id': r['id'], 'token': r['token']}
        e.change(**args)
        result = e.verify(**args)
        self.assertFalse(result['passed'])
        self.assertFalse(result['evidence']['source_unchanged'])
        self.assertIn('source symlinks', result['evidence']['source_error'])
        self.assertEqual(result['evidence']['results'][0]['exit_code'], 0)
        self.assertEqual(result['evidence']['results'][0]['stdout'], 'done\n')
        self.assertEqual(e.status(run_id=r['id'])['state'], 'changed')
        self.assertEqual(e.history(task)['events'][-1]['kind'], 'verification_failed')
