#!/usr/bin/env python3
"""Fake credentials only; public projections must not rewrite private evidence."""
import argparse
import base64
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

p = argparse.ArgumentParser()
p.add_argument('--installed', action='store_true')
a = p.parse_args()
ROOT = Path(__file__).resolve().parents[2]
if not a.installed:
    sys.path.insert(0, str(ROOT))
from agent_workflow.runner import redact, _private_metadata
from agent_workflow.evolution import _public_output
from agent_workflow.core import Engine


def main():
    fake = 'a' * 48
    opaque = 'FAKE_OPAQUE_VALUE_NOT_A_CREDENTIAL'
    api = 'sk-FAKEEXAMPLE-' + 'z' * 24
    cases = [
        ('cookie multiple fields', {'note': 'Cookie: first=FAKE_FIRST; session=FAKE_SESSION'}, 'FAKE_SESSION'),
        ('set-cookie fields', {'note': 'Set-Cookie: session=FAKE_SESSION; Path=/; HttpOnly'}, 'FAKE_SESSION'),
        ('quoted password spaces', {'note': 'password="FAKE FIRST SECOND"'}, 'FIRST SECOND'),
        ('single quoted password', {'note': "password='FAKE FIRST SECOND'"}, 'FIRST SECOND'),
        ('free fencing token', {'note': 'prefix ' + fake + ' suffix'}, fake),
        ('mixed credential key', {'AUTH_TOKEN': opaque}, opaque),
        ('API key field', {'api_key': opaque}, opaque),
        ('API key header', {'X-API-Key': opaque}, opaque),
        ('session token', {'session_token': opaque}, opaque),
        ('secret access key', {'aws_secret_access_key': opaque}, opaque),
        ('escaped slash quote JSON', {'note': json.dumps({'token': 'prefix\\\"PRIVATE_SLASH_SUFFIX'})}, 'PRIVATE_SLASH_SUFFIX'),
        ('escaped quote JSON', {'note': json.dumps({'token': 'prefix"PRIVATE_SUFFIX'})}, 'PRIVATE_SUFFIX'),
        ('nested JSON', {'note': json.dumps({'refresh_token': opaque})}, opaque),
        ('truncated JSON', {'note': '{"access_token":"' + opaque}, opaque),
        ('bearer header', {'note': 'Authorization: Bearer ' + opaque}, opaque),
        ('basic header', {'note': 'Authorization: Basic ' + base64.b64encode(b'fake:example-password').decode()}, base64.b64encode(b'fake:example-password').decode()),
        ('provider shape', {'note': api}, api),
        ('base64 fencing token', {'note': base64.b64encode(fake.encode()).decode()}, base64.b64encode(fake.encode()).decode()),
        ('encoded JSON', {'note': base64.b64encode(json.dumps({'api_key': opaque}).encode()).decode()}, base64.b64encode(json.dumps({'api_key': opaque}).encode()).decode()),
        ('hex fencing token', {'note': fake.encode().hex()}, fake.encode().hex()),
        ('escaped fencing token', {'note': ''.join('\\u%04x' % ord(c) for c in fake)}, ''.join('\\u%04x' % ord(c) for c in fake)),
        ('percent fencing token', {'note': ''.join('%%%02x' % ord(c) for c in fake)}, ''.join('%%%02x' % ord(c) for c in fake)),
        ('secret map key', {fake: 'inert value'}, fake),
        ('URL userinfo', {'note': 'https://example-user:FAKE_PASSWORD@example.invalid/path'}, 'FAKE_PASSWORD'),
    ]
    checked = []
    for name, export in [('runner', redact), ('evolution', _public_output)]:
        for label, value, secret in cases:
            before = copy.deepcopy(value)
            result = export(value)
            assert secret not in json.dumps(result), name + ': retained ' + label
            assert value == before, 'public export mutated caller private facts'
            assert export(result) == result, 'public policy is not idempotent: ' + label
            checked.append(name + ': ' + label)
        safe = {'source_sha256': 'b' * 64, 'hex_identity': 'api_key=FAKEVALUE'.ljust(32).encode().hex(), 'head': 'c' * 40, 'output_tokens': 123, 'token_count': 7, 'task_id': 'task-' + 'd' * 12,
                'repo': '/Users/example-person/owned-project', 'policy_body': '{"fixed":true,"budget":123}'}
        assert export(safe) == safe, 'canonical identities or usage counters changed'
        for safe_json in ('  { "fixed" : true, "budget":123 }  ',
                          json.dumps({'note': 'plain "quoted" text', 'path': r'C:\safe\file'})):
            assert export({'note': safe_json}) == {'note': safe_json}, 'safe JSON bytes changed'
        quoted = export({'note': json.dumps({'token': 'prefix"PRIVATE_SUFFIX'})})
        assert json.loads(quoted['note'])['token'] in ('REDACTED', '[REDACTED]')
        assert export(quoted) == quoted
        deep_json = {'token': 'DEEP_PRIVATE_SUFFIX'}
        for _ in range(30):
            deep_json = {'nested': deep_json}
        projected = export({'note': json.dumps(deep_json)})
        assert 'DEEP_PRIVATE_SUFFIX' not in json.dumps(projected)
        assert export(projected) == projected
    canonical = {'argv': ['/Users/example-person/private', fake], 'api_key': opaque, 'token': fake}
    expected_private = dict(canonical, token='REDACTED')
    assert _private_metadata(canonical) == expected_private, 'legacy canonical private metadata representation changed'
    assert canonical['token'] == fake
    from agent_workflow.redaction import public, POLICY_VERSION
    assert opaque not in json.dumps(public({'note': opaque}, known_secrets=[opaque]))
    assert public({'note': 'unlabelled opaque text'}) == {'note': 'unlabelled opaque text'}
    assert len(json.dumps(public({'note': 'x' * 200000}))) < 1000
    nested = {'token': opaque}
    for _ in range(30):
        nested = {'nested': nested}
    assert opaque not in json.dumps(public(nested))
    with tempfile.TemporaryDirectory(prefix='iterail-public-redaction-') as temp:
        root = Path(temp).resolve()
        repo = root / 'source'
        repo.mkdir()
        subprocess.run(['git', 'init', '-q', str(repo)], check=True, capture_output=True)
        (repo / 'data.txt').write_text('owned source\n')
        raw = json.dumps({'token': fake, 'api_key': opaque, 'note': api, 'escaped': {'token': 'prefix"PRIVATE_SUFFIX'}}) + '\n'
        config = {'repo': str(repo), 'tests': [[sys.executable, '-c', 'print(' + repr(raw.rstrip('\n')) + ')']], 'sources': ['local'],
                  'budget': {'lease_seconds': 30, 'run_seconds': 30, 'test_seconds': 10, 'max_attempts': 1},
                  'acceptance': {'require_tests': True, 'require_unchanged_candidate': True},
                  'permissions': {'execute_tests': True, 'network': False, 'publish': False}}
        e = Engine(root / 'state', config)
        task = e.import_task('private-evidence', 'Owned fake credential capture')['id']
        run = e.claim('local-no-model-fixture', task)
        own = {'run_id': run['id'], 'token': run['token']}
        e.change(**own)
        assert e.verify(**own)['passed']
        status = e.status(run_id=run['id'])
        evidence = json.loads(status['evidence'])
        result = evidence['results'][0]
        raw_hash = hashlib.sha256(raw.encode()).hexdigest()
        assert result['stdout'] == raw and hashlib.sha256(result['stdout'].encode()).hexdigest() == raw_hash
        private_before = json.dumps(status, sort_keys=True)
        for export in (redact, _public_output):
            projected = export({'raw_stdout_sha256': raw_hash, 'core_status': status})
            text = json.dumps(projected)
            assert fake not in text and opaque not in text and api not in text and 'PRIVATE_SUFFIX' not in text
            assert raw_hash in text, 'raw evidence hash was relabelled or removed'
        assert json.dumps(e.status(run_id=run['id']), sort_keys=True) == private_before
        e.db.close()
    print(json.dumps({'passed': True, 'policy_version': POLICY_VERSION, 'fake_credential_cases': len(checked),
                      'real_private_subprocess_evidence_preserved': True, 'raw_hash_semantics_unchanged': True,
                      'installed': a.installed, 'model_calls': 0}))


if __name__ == '__main__':
    main()
