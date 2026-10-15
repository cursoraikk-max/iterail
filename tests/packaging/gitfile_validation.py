"""Deterministic Git-boundary tests; validate_config itself is never mocked."""
import argparse
import copy
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

parser = argparse.ArgumentParser()
parser.add_argument('--installed', action='store_true')
OPTIONS, REMAINING = parser.parse_known_args()
SOURCE = Path(__file__).resolve().parents[2]
if not OPTIONS.installed:
    sys.path.insert(0, str(SOURCE))
from agent_workflow import core

if OPTIONS.installed:
    assert Path(core.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()), 'must test installed runtime'
else:
    assert Path(core.__file__).resolve() == SOURCE / 'agent_workflow/core.py', 'must test selected source'


class GitfileValidation(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='iterail-git-boundary-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.repo = self.root / 'selected'
        self.repo.mkdir()
        # Filesystem marker only; Git is the sole mocked external boundary.
        (self.repo / '.git').write_text('gitdir: fixture-only\n')
        self.config = {
            'repo': str(self.repo), 'tests': [[sys.executable, '-c', 'pass']], 'sources': ['local'],
            'budget': {'lease_seconds': 60, 'run_seconds': 120, 'test_seconds': 30, 'max_attempts': 1},
            'acceptance': {'require_tests': True, 'require_unchanged_candidate': True},
            'permissions': {'execute_tests': True, 'network': False, 'publish': False}}
        self.argv = ['git', '-C', str(self.repo), 'rev-parse', '--show-toplevel']

    def reject(self, *, result=None, error=None):
        original = copy.deepcopy(self.config)
        with patch.dict(os.environ, {'GIT_DIR': 'untrusted-fixture',
                                     'GIT_WORK_TREE': 'untrusted-fixture',
                                     'ITERAIL_TEST_SECRET': 'synthetic-do-not-forward'}), \
                patch.object(core.subprocess, 'run', return_value=result, side_effect=error) as boundary:
            with self.assertRaisesRegex(core.WorkflowError, 'repo must be a local git checkout'):
                core.validate_config(self.config)
        boundary.assert_called_once()
        self.assertEqual(boundary.call_args.args, (self.argv,))
        options = boundary.call_args.kwargs
        self.assertEqual(options['timeout'], 10)
        self.assertTrue(options['capture_output'])
        self.assertTrue(options['text'])
        env = options['env']
        for forbidden in ['GIT_DIR', 'GIT_WORK_TREE', 'ITERAIL_TEST_SECRET', 'HOME']:
            self.assertNotIn(forbidden, env)
        self.assertEqual(env['GIT_CONFIG_GLOBAL'], os.devnull)
        self.assertEqual(env['GIT_CONFIG_NOSYSTEM'], '1')
        self.assertEqual(env['GIT_OPTIONAL_LOCKS'], '0')
        self.assertEqual(self.config, original)

    def test_different_git_root_is_rejected(self):
        other = self.root / 'other'
        other.mkdir()
        self.reject(result=subprocess.CompletedProcess(self.argv, 0, str(other) + '\n', ''))

    def test_missing_git_is_rejected(self):
        self.reject(error=FileNotFoundError('synthetic missing Git executable'))

    def test_git_timeout_is_rejected_without_waiting(self):
        self.reject(error=subprocess.TimeoutExpired(self.argv, 10))

    def test_directory_fast_path_preserves_legacy_semantics(self):
        (self.repo / '.git').unlink()
        (self.repo / '.git').mkdir()
        # The existing directory branch accepts this marker without a Git query;
        # this test makes no stronger repository-integrity or security claim.
        with patch.object(core.subprocess, 'run', side_effect=AssertionError('unexpected Git query')) as boundary:
            self.assertEqual(core.validate_config(self.config), self.config)
        boundary.assert_not_called()


if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0], *REMAINING], verbosity=2)
