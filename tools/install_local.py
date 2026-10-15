#!/usr/bin/env python3
"""Offline isolated install and CLI/MCP smoke; no client settings or model calls."""
import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def run_command(out, rows, env, label, argv, input_text=None, limit=120):
    """Retain failures including timeouts; this is not process-tree isolation."""
    timed_out = False
    try:
        result = subprocess.run([str(x) for x in argv], cwd=out, env=env,
                                input=input_text, text=True, capture_output=True, timeout=limit)
        stdout, stderr, code = result.stdout, result.stderr, result.returncode
    except subprocess.TimeoutExpired as exc:
        # TimeoutExpired output can be bytes even with text=True.
        def decode(value):
            return value.decode('utf-8', 'replace') if isinstance(value, bytes) else value or ''
        stdout, stderr, code = decode(exc.stdout), decode(exc.stderr), 124
        timed_out = True
    (out / (label + '.stdout')).write_text(stdout)
    (out / (label + '.stderr')).write_text(stderr)
    rows.append({'label': label, 'argv': [str(x) for x in argv],
                 'exit_code': code, 'timed_out': timed_out})
    (out / 'commands.json').write_text(json.dumps(rows, indent=2) + '\n')
    if code:
        raise RuntimeError(label + (' timed out' if timed_out else ' failed')
                           + '; retained logs in ' + str(out)
                           + '; no process-tree isolation guarantee')
    return stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='new absolute directory outside source')
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    if os.name != 'posix' or sys.version_info < (3, 11):
        parser.error('requires POSIX and Python 3.11+; use WSL on Windows')
    if not shutil.which('git'):
        parser.error('Git missing: request installation using the OS official package manager')
    try:
        versions = {name: importlib.metadata.version(name) for name in ('setuptools', 'wheel', 'pip')}
        if int(versions['setuptools'].split('.')[0]) < 77:
            raise ValueError('setuptools>=77 required')
        if tuple(int(x) for x in versions['pip'].split('.')[:2]) < (22, 3):
            raise ValueError('pip>=22.3 required for --python')
    except (importlib.metadata.PackageNotFoundError, ValueError) as exc:
        parser.error(str(exc) + '; see docs/AI-INSTALL.md for a separate local build environment')
    if args.output:
        out = args.output
        if not out.is_absolute() or out.exists() or out.is_symlink() or out.resolve().is_relative_to(repo):
            parser.error('--output must be a new absolute directory outside source; never reuse an environment')
        out.mkdir(mode=0o700, parents=True)
    else:
        temporary_root = Path(tempfile.gettempdir()).resolve()
        if temporary_root.is_relative_to(repo):
            parser.error('temporary directory is inside source; select --output outside source or a different TMPDIR')
        out = Path(tempfile.mkdtemp(prefix='iterail-install-', dir=temporary_root))
    out = out.resolve()
    if out.is_relative_to(repo):
        parser.error('installation output must be outside source')
    home = out / 'home'
    home.mkdir(mode=0o700)
    env = {'PATH': str(Path(sys.executable).parent) + os.pathsep + os.environ.get('PATH', os.defpath),
           'HOME': str(home), 'LC_ALL': 'C', 'TZ': 'UTC',
           'PIP_NO_INDEX': '1', 'PIP_CONFIG_FILE': os.devnull,
           'PIP_DISABLE_PIP_VERSION_CHECK': '1', 'PYTHONDONTWRITEBYTECODE': '1',
           'PYTHONNOUSERSITE': '1', 'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1'}
    rows = []

    def run(label, argv, input_text=None, limit=120):
        return run_command(out, rows, env, label, argv, input_text, limit)

    built = json.loads(run('build', [sys.executable, '-B', repo / 'tools/build_offline.py',
                                    '--output', out / 'build'], limit=610).strip().splitlines()[-1])
    run('venv', [sys.executable, '-m', 'venv', '--without-pip', out / 'venv'])
    python = out / 'venv/bin/python'
    run('install', [sys.executable, '-m', 'pip', '--python', python, 'install',
                    '--no-index', '--no-deps', '--no-cache-dir', built['wheel']])
    cli = [python, '-I', '-B', '-m', 'agent_workflow', '--state', out / 'smoke-state']
    probe = out / 'smoke-repo'
    probe.mkdir()
    run('git', ['git', '-c', 'init.templateDir=', 'init', '-q', probe])
    config = out / 'smoke-project.json'
    config.write_text(json.dumps({
        'repo': str(probe), 'tests': [[str(python), '-c', 'pass']], 'sources': ['local'],
        'budget': {'lease_seconds': 60, 'run_seconds': 120, 'test_seconds': 30, 'max_attempts': 1},
        'acceptance': {'require_tests': True, 'require_unchanged_candidate': True},
        'permissions': {'execute_tests': True, 'publish': False, 'network': False}}))
    initialized = json.loads(run('init', cli + ['--config', config, 'init']))
    assert initialized['ok'], initialized
    caps = json.loads(run('cli', cli + ['capabilities']))
    assert caps['ok'], caps
    requests = [
        {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
            'protocolVersion': '2025-06-18', 'capabilities': {},
            'clientInfo': {'name': 'iterail-install-smoke', 'version': '1'}}},
        {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
        {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list', 'params': {}},
        {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
         'params': {'name': 'capabilities', 'arguments': {}}},
    ]
    replies = {r['id']: r for r in map(json.loads, run('mcp', cli + ['mcp'],
               ''.join(json.dumps(r) + '\n' for r in requests)).splitlines())}
    assert sorted(replies) == [1, 2, 3] and all('error' not in r for r in replies.values())
    names = sorted(t['name'] for t in replies[2]['result']['tools'])
    assert names == caps['result']['interfaces']['mcp']['tools']
    result = replies[3]['result']
    assert result['isError'] is False and json.loads(result['content'][0]['text']) == caps['result']
    fragment = {'command': str(python), 'args': ['-I', '-B', '-m', 'agent_workflow',
                '--state', str(out / 'smoke-state'), 'mcp']}
    (out / 'mcp-command.json').write_text(json.dumps(fragment, indent=2) + '\n')
    summary = {'passed': True, 'output': str(out), 'python': str(python),
               'toolchain': versions, 'mcp_tools': len(names), 'model_calls': 0,
               'downloads': 0, 'client_config_changed': False,
               'smoke_state_only': True, 'mcp_command_file': str(out / 'mcp-command.json')}
    (out / 'install-summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary))


if __name__ == '__main__':
    main()
