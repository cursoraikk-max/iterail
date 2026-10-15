"""Packaged byte-identical independent contracts, exercised as real processes."""
import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PortableContracts(unittest.TestCase):
    def check_contract(self, name):
        script = ROOT / 'tests' / 'contracts' / 'phase2' / (name + '.py')
        process = subprocess.run([sys.executable, str(script), '--repo', str(ROOT)],
                                 cwd=ROOT, capture_output=True, text=True, timeout=90)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        report = json.loads(process.stdout)
        self.assertIs(report['passed'], True)
        self.assertIs(report['source_stable'], True)

    def test_producer_contract(self):
        self.check_contract('producer')

    def test_memory_contract(self):
        self.check_contract('memory')

    def test_handoff_contract(self):
        self.check_contract('handoff')
