"""Bounded runtime capability snapshots, never execution of project test argv."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def executable(value):
    found = str(Path(value).absolute()) if Path(value).is_absolute() else shutil.which(value)
    if not found or not Path(found).is_file() or not os.access(found, os.X_OK):
        raise ValueError('preflight executable unavailable: '+value)
    path = Path(found)
    return {'path': str(path), 'resolved_path': str(path.resolve()), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def probe(argv):
    result = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, timeout=5,
                            env={'PATH': os.defpath, 'LANG': 'C', 'PYTHONDONTWRITEBYTECODE': '1'})
    if result.returncode or len(result.stdout)+len(result.stderr) > 131072:
        raise ValueError('preflight capability probe failed')
    return result.stdout.decode('utf-8', 'strict')


def snapshot(policy):
    """Owner-selected Python metadata and direct executable features only."""
    errors = []
    observations = {'python': None, 'commands': [], 'tests': policy['tests'], 'cwd': policy['repo']}
    try:
        observations['python'] = executable(policy['python'])
        code = ('import json,sys,importlib.metadata as m; out={"executable":sys.executable,"version":list(sys.version_info[:3]),"distributions":{}}\n'
                'for name in json.loads(sys.argv[1]):\n'
                ' try:out["distributions"][name]=m.version(name)\n'
                ' except m.PackageNotFoundError:out["distributions"][name]=None\n'
                'print(json.dumps(out))')
        info = json.loads(probe([observations['python']['path'], '-I', '-B', '-c', code,
                                 json.dumps(list(policy['build_requirements']))]))
        observations['python']['metadata'] = info
        if tuple(info['version']) < (3, 11):
            errors.append('selected Python requires >=3.11')
        for name, minimum in policy['build_requirements'].items():
            actual = info['distributions'][name]
            def version(value):
                if not isinstance(value, str) or not re.fullmatch(r'\d+(?:\.\d+)*', value):
                    raise ValueError('preflight distribution version unavailable: '+name)
                parts=[int(x) for x in value.split('.')]
                while parts and parts[-1]==0:parts.pop()
                return tuple(parts)
            try:
                if version(actual) < version(minimum):
                    errors.append('distribution too old: '+name)
            except ValueError as error:
                errors.append(str(error))
    except (OSError, ValueError, subprocess.TimeoutExpired, KeyError) as error:
        errors.append('Python preflight: '+str(error))
    for argv in policy['tests']:
        try:
            item = executable(argv[0]); name = Path(item['path']).name.lower()
            if name in {'env', 'sh', 'bash', 'zsh', 'cmd', 'powershell', 'pwsh'}:
                raise ValueError('opaque test launcher requires a direct executable')
            if name.startswith('python'):
                item['version'] = probe([item['path'], '-I', '-B', '-c', 'import sys;print(".".join(map(str,sys.version_info[:3])))']).strip()
            if '--test' in argv:
                if not name.startswith('node'):
                    raise ValueError('--test requires an inspectable direct Node executable')
                item['version'] = probe([item['path'], '--version']).strip()
                help_text = probe([item['path'], '--help'])
                if not re.fullmatch(r'v\d+\.\d+\.\d+', item['version']) or int(item['version'].split('.')[0][1:]) < 18 or not re.search(r'(?m)^\s*--test(?:\s|=|$)', help_text):
                    raise ValueError('Node runtime does not support --test')
            observations['commands'].append(item)
        except (OSError, ValueError, subprocess.TimeoutExpired) as error:
            errors.append(str(error))
    return {'passed': not errors, 'errors': errors, 'observations': observations,
            'fingerprint': hashlib.sha256(encoded(observations)).hexdigest(),
            'coordinator_python': {'executable': sys.executable, 'version': list(sys.version_info[:3])},
            'executes_project_tests': False, 'provider_ready': False}
