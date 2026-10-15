"""Portable copy of the independently frozen offline protocol contract."""
import json
from pathlib import Path
import subprocess
import sys
import unittest


class AppServerContract(unittest.TestCase):
    def test_independent_supplemental_regressions(self):
        root = Path(__file__).resolve().parents[1]
        for name in ('appserver-postterminal-probe.py', 'appserver-correlation-bound-probe.py'):
            with self.subTest(probe=name):
                result = subprocess.run([sys.executable, str(root / 'tests/contracts/phase3' / name), str(root)],
                                        capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertTrue(json.loads(result.stdout)['passed'])

    def test_frozen_contract(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run([sys.executable, str(root / 'tests/contracts/phase3/appserver.py'), str(root)],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        proof = json.loads(result.stdout)
        self.assertTrue(proof['passed'])
        self.assertEqual(proof['evidence']['external_backend_calls'], 0)


if __name__ == '__main__':
    unittest.main()
