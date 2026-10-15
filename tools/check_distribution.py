#!/usr/bin/env python3
"""Check installed wheel and rebuilt sdist with real CLI/MCP; no downloads."""
import argparse
import email
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import zipfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    out = args.output
    if not out.is_absolute() or out.exists() or out.resolve().is_relative_to(repo):
        parser.error('output must be a new absolute directory outside source')
    out.mkdir(parents=True, mode=0o700)
    out = out.resolve()
    summaries = []
    commands = []

    def run(label, argv, cwd):
        result = subprocess.run([str(x) for x in argv], cwd=cwd, capture_output=True,
                                text=True, timeout=900)
        (out / (label + '.stdout')).write_text(result.stdout)
        (out / (label + '.stderr')).write_text(result.stderr)
        commands.append({'label': label, 'exit_code': result.returncode})
        (out / 'commands.json').write_text(json.dumps(commands, indent=2) + '\n')
        if result.returncode:
            raise RuntimeError(label + ' failed; inspect logs in ' + str(out))
        return result.stdout

    def install(source, label):
        target = out / label
        summary = json.loads(run(label, [sys.executable, '-B', source / 'tools/install_local.py',
                                        '--output', target], out))
        assert summary['passed'] and summary['mcp_tools'] == 27 and summary['downloads'] == 0
        data = target / 'venv/share/agent-workflow'
        required = ['README.md', 'README.zh-CN.md', 'LICENSE', 'SECURITY.md', 'CONTRIBUTING.md',
                    'UPGRADING.md', 'CHANGELOG.md', 'skills/agent-workflow/SKILL.md',
                    'assets/brand/logo-light.svg', 'assets/brand/logo-dark.svg']
        required += [str(p.relative_to(repo)) for p in (repo / 'docs').glob('*.md')]
        for name in required:
            assert (data / name).read_bytes() == (repo / name).read_bytes(), name
        build = json.loads((target / 'build/build-summary.json').read_text())
        with zipfile.ZipFile(build['wheel']) as wheel:
            metadata = email.message_from_bytes(wheel.read(next(n for n in wheel.namelist()
                                                       if n.endswith('.dist-info/METADATA'))))
            assert metadata['Name'] == 'agent-workflow' and metadata['Version'] == '0.1.0a1'
            assert not metadata.get_all('Requires-Dist')
            runtime = {str(p.relative_to(repo)): p.read_bytes() for p in (repo / 'agent_workflow').glob('*.py')}
            assert {n: wheel.read(n) for n in wheel.namelist()
                    if n.startswith('agent_workflow/') and n.endswith('.py')} == runtime
        summaries.append({'kind': label, 'passed': True, 'mcp_tools': summary['mcp_tools'],
                          'exact_installed_document_files': len(required),
                          'wheel_sha256': build['sha256']['wheel']})
        return Path(build['sdist'])

    sdist = install(repo, 'wheel')
    extracted = out / 'extracted'
    extracted.mkdir()
    with tarfile.open(sdist) as archive:
        members = archive.getmembers()
        roots = {Path(m.name).parts[0] for m in members}
        assert len(roots) == 1
        for member in members:
            path = Path(member.name)
            assert not path.is_absolute() and '..' not in path.parts
            assert member.isfile() or member.isdir()
            assert member.uid == member.gid == 0 and member.uname == member.gname == ''
            target = extracted / path
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.extractfile(member).read())
    source = extracted / next(iter(roots))
    for p in repo.rglob('*'):
        relative = p.relative_to(repo)
        if any(n in {'.git', '.github', '.workflow', '__pycache__'} for n in relative.parts) or p.suffix == '.pyc' or relative == Path('.gitignore'):
            continue
        if p.is_file():
            assert (source / relative).read_bytes() == p.read_bytes(), str(relative)
    install(source, 'sdist-rebuilt-wheel')
    report = {'passed': True, 'checks': summaries, 'model_calls': 0, 'downloads': 0,
              'actual_client_connected': False, 'sdist_install': 'offline backend rebuild then wheel install'}
    (out / 'summary.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
