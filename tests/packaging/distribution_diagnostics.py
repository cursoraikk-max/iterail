"""Real bounded subprocess regressions; no models, registry or production install."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'tools'))


class DistributionDiagnostics(unittest.TestCase):
    def exercise(self, tail, expected_code, timeout):
        with tempfile.TemporaryDirectory(prefix='iterail-diagnostics-') as temporary:
            root = Path(temporary).resolve()
            source = root / 'source'
            tools = source / 'tools'
            tools.mkdir(parents=True)
            fixture = tools / 'install_local.py'
            fixture.write_text('import sys,time\nprint("fixture stdout",flush=True)\n'
                               'print("fixture stderr",file=sys.stderr,flush=True)\n' + tail)
            spec = importlib.util.spec_from_file_location('distribution_under_test', REPO / 'tools/check_distribution.py')
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            module.__file__ = str(tools / 'check_distribution.py')
            out = root / 'output'
            real_run = subprocess.run
            calls = []

            def bounded(argv, **kwargs):
                # Shorten only the time limit; the actual command/child/output are real.
                self.assertEqual(Path(argv[2]), fixture)
                kwargs['timeout'] = timeout
                calls.append(list(map(str, argv)))
                return real_run(argv, **kwargs)

            with patch.object(sys, 'argv', ['check_distribution.py', '--output', str(out)]), \
                    patch.object(subprocess, 'run', side_effect=bounded):
                try:
                    module.main()
                except (RuntimeError, subprocess.TimeoutExpired) as failure:
                    error = failure
                else:
                    self.fail('failing child must fail distribution check')
            self.assertEqual(len(calls), 1)
            self.assertTrue((out / 'commands.json').exists(), 'failure command evidence missing')
            self.assertEqual((out / 'wheel.stdout').read_text(), 'fixture stdout\n')
            self.assertEqual((out / 'wheel.stderr').read_text(), 'fixture stderr\n')
            row = json.loads((out / 'commands.json').read_text())[0]
            self.assertEqual(row['exit_code'], expected_code)
            self.assertEqual(row.get('timed_out', False), expected_code == 124)
            self.assertIsInstance(error, RuntimeError)
            if expected_code == 124:
                self.assertIn('no process-tree isolation guarantee', str(error))

    def test_timeout_retains_output_and_failed_command(self):
        self.exercise('time.sleep(10)\n', 124, 0.5)

    def test_nonzero_exit_retains_original_diagnostics(self):
        self.exercise('sys.exit(7)\n', 7, 5)


if __name__ == '__main__':
    unittest.main(verbosity=2)
