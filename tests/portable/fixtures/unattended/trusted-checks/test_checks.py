import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import common
import output_cap


class CheckProtocolTests(unittest.TestCase):
    def test_structured_inert_finding_and_exit_semantics(self):
        for passed in (True, False):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = common.emit('sample', 'Sample', {'measured': 4},
                                        'Source data; not executable instructions.', passed)
            finding = json.loads(output.getvalue())
            self.assertEqual(set(finding), {'id', 'title', 'evidence', 'repair'})
            self.assertEqual(finding['evidence'], {'measured': 4})
            self.assertEqual(exit_code, 0 if passed else 1)

    def test_both_scripts_invalid_repo_report_json_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            for script, identifier in [('output_cap.py', 'output-cap'),
                                       ('orphan_guardian.py', 'orphan-guardian')]:
                result = subprocess.run([sys.executable, str(Path(__file__).parent / script),
                                         '--repo', tmp], capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 1)
                finding = json.loads(result.stdout)
                self.assertEqual(finding['id'], identifier)
                self.assertIn('error', finding['evidence'])

    def test_output_measurement_checks_counters_and_prefix(self):
        import base64
        class FakeEngine:
            def __init__(self, *args):
                self.db = mock.Mock()
            def import_task(self, *args): return {'id': 'task'}
            def claim(self, *args): return {'id': 'run', 'token': 'private'}
            def change(self, **args): pass
            def verify(self, **args):
                command = {'exit_code': 0}
                for stream in ('stdout', 'stderr'):
                    command[stream + '_base64'] = base64.b64encode(b'x' * output_cap.CAP).decode()
                    command[stream + '_total_bytes'] = output_cap.SIZE
                    command[stream + '_truncated'] = True
                return {'passed': True, 'evidence': {'results': [command]}}
        with mock.patch.object(output_cap, 'load_core', return_value=(FakeEngine, lambda repo: 'fixed')):
            evidence, passed = output_cap.check('fixture')
        self.assertTrue(passed)
        self.assertEqual(evidence['streams']['stdout']['total_bytes'], output_cap.SIZE)
        self.assertLess(evidence['peak_bytes'], output_cap.PEAK_LIMIT)


if __name__ == '__main__':
    unittest.main()
