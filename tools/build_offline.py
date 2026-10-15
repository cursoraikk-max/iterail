#!/usr/bin/env python3
"""Build locally with installed tooling; normalize only sdist owner headers.

No downloads, dependency resolver, provider or source-directory build output.
"""
import argparse
import copy
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import time


def normalize_sdist(path):
    """Preserve member paths/types/modes/bytes; remove personal owner metadata."""
    temporary = path.with_name(path.name + '.neutral')
    ownership = {'uid', 'gid', 'uname', 'gname', 'atime', 'ctime',
                 'SCHILY.uid', 'SCHILY.gid', 'SCHILY.uname', 'SCHILY.gname',
                 'SCHILY.atime', 'SCHILY.ctime'}
    before = []
    with tarfile.open(path, 'r:gz') as source, temporary.open('wb') as raw:
        with gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode='w', format=tarfile.PAX_FORMAT) as target:
                for member in source.getmembers():
                    name = Path(member.name)
                    if name.is_absolute() or '..' in name.parts or not (member.isfile() or member.isdir()):
                        raise ValueError('only safe regular files and directories may enter sdist')
                    content = source.extractfile(member).read() if member.isfile() else None
                    before.append((member.name, member.type, member.mode, content))
                    neutral = copy.copy(member)
                    neutral.uid = neutral.gid = 0
                    neutral.uname = neutral.gname = ''
                    neutral.pax_headers = {k: v for k, v in member.pax_headers.items() if k not in ownership}
                    if content is None:
                        target.addfile(neutral)
                    else:
                        import io
                        target.addfile(neutral, io.BytesIO(content))
    with tarfile.open(temporary, 'r:gz') as result:
        after = []
        for member in result.getmembers():
            if member.uid or member.gid or member.uname or member.gname or ownership.intersection(member.pax_headers):
                raise ValueError('sdist owner metadata normalization failed')
            after.append((member.name, member.type, member.mode,
                          result.extractfile(member).read() if member.isfile() else None))
    if before != after:
        raise ValueError('normalization changed sdist member identity or content')
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--deadline', type=float)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    output = args.output
    if not output.is_absolute() or output.exists() or output.resolve().is_relative_to(repo):
        raise ValueError('output must be a new absolute directory outside source')
    deadline = args.deadline if args.deadline is not None else time.time() + 600
    if not math.isfinite(deadline) or time.time() >= deadline:
        raise ValueError('build deadline exhausted')
    output.mkdir(parents=True, mode=0o700)
    source = output / 'source-copy'
    shutil.copytree(repo, source, ignore=shutil.ignore_patterns('.git', '.workflow', '__pycache__', '*.pyc'))
    dist = output / 'dist'
    dist.mkdir()
    home = output / 'home'
    home.mkdir(mode=0o700)
    env = {'PATH': str(Path(sys.executable).parent) + os.pathsep + os.defpath,
           'HOME': str(home), 'LC_ALL': 'C', 'TZ': 'UTC',
           'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONNOUSERSITE': '1',
           'PIP_NO_INDEX': '1', 'PIP_CONFIG_FILE': os.devnull,
           'PIP_DISABLE_PIP_VERSION_CHECK': '1',
           'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1'}
    artifacts = {}
    for kind in ('wheel', 'sdist'):
        remaining = deadline - time.time()
        if remaining <= 0:
            raise ValueError('build deadline exhausted')
        code = ('import json,setuptools.build_meta as b; print(json.dumps({"artifact":b.build_'
                + kind + '(' + repr(str(dist)) + ')}))')
        command = [sys.executable, '-B', '-c', code]
        result = subprocess.run(command, cwd=source, env=env, capture_output=True, text=True,
                                timeout=remaining)
        (output / (kind + '.stdout')).write_text(result.stdout)
        (output / (kind + '.stderr')).write_text(result.stderr)
        if result.returncode:
            raise RuntimeError(kind + ' build failed; inspect external build logs')
        artifact = dist / json.loads(result.stdout.strip().splitlines()[-1])['artifact']
        if not artifact.is_file() or artifact.parent != dist:
            raise ValueError('backend did not produce the expected artifact')
        artifacts[kind] = artifact
    normalize_sdist(artifacts['sdist'])
    if time.time() >= deadline:
        raise ValueError('build exceeded original deadline')
    report = {kind: str(path) for kind, path in artifacts.items()}
    report.update(offline=True, model_calls=0,
                  sha256={kind: hashlib.sha256(path.read_bytes()).hexdigest() for kind, path in artifacts.items()})
    (output / 'build-summary.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
