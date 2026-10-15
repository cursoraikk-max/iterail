import json
from pathlib import Path
import subprocess
import sys


def load_core(repo):
    sys.path.insert(0, str(Path(repo).resolve()))
    from agent_workflow.core import Engine, digest
    return Engine, digest


def configuration(repo, command):
    return {'repo': str(repo), 'tests': [command], 'sources': ['local'],
            'budget': {'lease_seconds': 20, 'run_seconds': 30, 'test_seconds': 10, 'max_attempts': 2},
            'acceptance': {'require_tests': True, 'require_unchanged_candidate': True},
            'permissions': {'execute_tests': True, 'publish': False, 'network': False}}


def fixture_repo(root):
    repo = root / 'repo'
    repo.mkdir()
    subprocess.run(['git', 'init', '-q', str(repo)], check=True, capture_output=True)
    (repo / 'code.py').write_text('x = 1\n')
    (repo / '.workflow').mkdir()
    return repo


def emit(identifier, title, evidence, repair, passed):
    print(json.dumps({'id': identifier, 'title': title, 'evidence': evidence, 'repair': repair}, sort_keys=True))
    return 0 if passed else 1
