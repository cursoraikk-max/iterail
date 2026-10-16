#!/usr/bin/env python3
"""Pinned incremental quality checks; artifacts stay outside the source tree."""
import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    out = args.output
    if not out.is_absolute() or out.exists() or out.is_symlink() or out.resolve().is_relative_to(repo):
        parser.error('--output must be a new absolute directory outside source')
    expected = {'ruff': '0.11.13', 'mypy': '1.15.0', 'coverage': '7.8.2',
                'mypy_extensions': '1.1.0', 'typing_extensions': '4.13.2'}
    try:
        versions = {name: importlib.metadata.version(name) for name in expected}
    except importlib.metadata.PackageNotFoundError as error:
        parser.error(str(error) + '; install requirements-dev.txt in a separate project venv')
    if versions != expected:
        parser.error('quality versions differ from requirements-dev.txt: ' + json.dumps(versions))
    out.mkdir(mode=0o700, parents=True)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', COVERAGE_FILE=str(out / '.coverage'))
    commands = [
        ('ruff', [sys.executable, '-m', 'ruff', 'check', '--no-cache',
                  'agent_workflow/redaction.py', 'tests/packaging/public_redaction.py', 'tools/check_quality.py']),
        ('mypy', [sys.executable, '-m', 'mypy', '--cache-dir', str(out / 'mypy-cache'), 'agent_workflow/redaction.py']),
        ('coverage-run', [sys.executable, '-m', 'coverage', 'run', '--branch', '--source=agent_workflow.redaction',
                          'tests/packaging/public_redaction.py']),
        ('coverage-report', [sys.executable, '-m', 'coverage', 'report', '--show-missing']),
        ('coverage-json', [sys.executable, '-m', 'coverage', 'json', '-o', str(out / 'coverage.json')]),
    ]
    rows = []
    for label, argv in commands:
        result = subprocess.run(argv, cwd=repo, env=env, capture_output=True, text=True, timeout=120)
        (out / (label + '.stdout')).write_text(result.stdout)
        (out / (label + '.stderr')).write_text(result.stderr)
        rows.append({'label': label, 'argv': argv, 'exit_code': result.returncode})
        if result.returncode:
            break
    value = {'passed': len(rows) == len(commands) and all(x['exit_code'] == 0 for x in rows),
             'versions': versions, 'scope': 'new public redaction module and focused regression/tool; not whole legacy tree',
             'coverage_threshold': None, 'commands': rows, 'model_calls': 0}
    (out / 'summary.json').write_text(json.dumps(value, indent=2) + '\n')
    print(json.dumps(value))
    return 0 if value['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
