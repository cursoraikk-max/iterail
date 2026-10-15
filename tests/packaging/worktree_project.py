"""Real local Git linked-worktree CLI/MCP regression; no remote or model calls."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

parser = argparse.ArgumentParser()
parser.add_argument('--python', default=sys.executable)
parser.add_argument('--installed', action='store_true')
OPTIONS, REMAINING = parser.parse_known_args()
SOURCE = Path(__file__).resolve().parents[2]


class WorktreeProject(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='iterail-worktree-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1',
                        PYTHONDONTWRITEBYTECODE='1')
        for key in list(self.env):
            if key.startswith('GIT_') and key not in {'GIT_CONFIG_GLOBAL', 'GIT_CONFIG_NOSYSTEM'}:
                del self.env[key]
        if OPTIONS.installed:
            self.env.pop('PYTHONPATH', None)
        else:
            self.env['PYTHONPATH'] = str(SOURCE)
        self.main = self.root / 'main'
        self.git('init', '-q', self.main)
        (self.main / 'note.txt').write_text('before\n')
        self.git('-C', self.main, 'add', '.')
        self.git('-C', self.main, '-c', 'user.name=Local fixture',
                 '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'Fixture')
        self.worktree = self.root / 'linked worktree'
        self.git('-C', self.main, 'worktree', 'add', '--detach', self.worktree, 'HEAD')
        self.assertTrue((self.worktree / '.git').is_file())
        self.assertEqual(self.git('-C', self.worktree, 'rev-parse', '--show-toplevel').strip(), str(self.worktree))
        self.state = self.root / 'state'

    def git(self, *args):
        return subprocess.run(['git', *map(str, args)], env=self.env, text=True,
                              capture_output=True, check=True, timeout=20).stdout

    def configure(self, repo):
        path = self.root / 'project.json'
        path.write_text(json.dumps({
            'repo': str(repo),
            'tests': [[OPTIONS.python, '-c', "from pathlib import Path; assert Path('note.txt').read_text() == 'after\\n'"]],
            'sources': ['local'],
            'budget': {'lease_seconds': 120, 'run_seconds': 180, 'test_seconds': 30, 'max_attempts': 1},
            'acceptance': {'require_tests': True, 'require_unchanged_candidate': True},
            'permissions': {'execute_tests': True, 'network': False, 'publish': False}}))
        return path

    def command(self):
        return [OPTIONS.python, *(['-I'] if OPTIONS.installed else []), '-B', '-m',
                'agent_workflow', '--state', str(self.state)]

    def call(self, action, args=None, config=None, success=True):
        argv = self.command() + (['--config', str(config)] if config else [])
        p = subprocess.run(argv + [action, '--args', json.dumps(args or {})], cwd=self.root,
                           env=self.env, capture_output=True, text=True, timeout=40)
        self.assertEqual(p.returncode, 0 if success else 1, p.stderr + p.stdout)
        result = json.loads(p.stdout)
        self.assertEqual(result['ok'], success)
        return result.get('result', result)

    def test_real_worktree_lifecycle_and_mcp(self):
        self.call('init', config=self.configure(self.worktree))
        task = self.call('import', {'external_key': 'worktree:001', 'title': 'Local worktree probe'})
        run = self.call('claim', {'worker': 'worktree-regression'})
        owned = {'run_id': run['id'], 'token': run['token']}
        self.call('progress', dict(owned, note='Actual local worktree probe'))
        (self.worktree / 'note.txt').write_text('after\n')
        self.call('change', owned)
        self.assertTrue(self.call('verify', owned)['passed'])
        self.call('transition', dict(owned, target='ready_review'))
        status = self.call('status', {'task_id': task['id']})
        self.assertEqual(status['state'], 'ready_review')
        requests = [
            {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
                'protocolVersion': '2025-06-18', 'capabilities': {},
                'clientInfo': {'name': 'worktree-regression', 'version': '1'}}},
            {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
            {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
             'params': {'name': 'status', 'arguments': {'task_id': task['id']}}},
        ]
        p = subprocess.run(self.command() + ['mcp'], cwd=self.root, env=self.env,
                           input=''.join(json.dumps(r) + '\n' for r in requests),
                           capture_output=True, text=True, timeout=40)
        self.assertEqual(p.returncode, 0, p.stderr)
        replies = {r['id']: r for r in map(json.loads, p.stdout.splitlines())}
        self.assertNotIn('error', replies[1])
        self.assertFalse(replies[2]['result']['isError'])
        self.assertEqual(json.loads(replies[2]['result']['content'][0]['text']), status)

    def test_git_environment_cannot_redirect_worktree_validation(self):
        self.env['GIT_DIR'] = str(self.root / 'missing-git-metadata')
        self.env['GIT_WORK_TREE'] = str(self.main)
        self.call('init', config=self.configure(self.worktree))

    def test_broken_gitfile_is_rejected(self):
        fake = self.root / 'fake'
        fake.mkdir()
        (fake / '.git').write_text('gitdir: missing\n')
        result = self.call('init', config=self.configure(fake), success=False)
        self.assertIn('local git checkout', result['error'])

    def test_nested_directory_cannot_inherit_parent_checkout(self):
        nested = self.main / 'nested'
        nested.mkdir()
        result = self.call('init', config=self.configure(nested), success=False)
        self.assertIn('local git checkout', result['error'])


if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0], *REMAINING], verbosity=2)
