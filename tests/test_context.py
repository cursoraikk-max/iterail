"""Portable frozen context contract and adapter boundary regressions."""
import base64
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from agent_workflow import context
from agent_workflow.core import Engine, WorkflowError, digest
from tests.test_core import config

ROOT = Path(__file__).resolve().parents[1]


class ContextTests(unittest.TestCase):
    def test_portable_frozen_contract(self):
        run = subprocess.run([sys.executable, str(ROOT / 'tests/contracts/phase3/context.py'), str(ROOT)],
                             text=True, capture_output=True, timeout=20)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertTrue(json.loads(run.stdout)['passed'])

    def test_independent_sensitive_boundary_regression(self):
        run = subprocess.run([sys.executable, str(ROOT / 'tests/contracts/phase3/context-boundary-probe.py'), str(ROOT)],
                             text=True, capture_output=True, timeout=20)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertTrue(json.loads(run.stdout)['passed'])

    def test_streamed_identity_complete_utf8_and_source_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'rules').write_bytes('é\r\n'.encode() * 500)
            (root / 'large').write_bytes(b'x' * 200000)
            (root / '.env').write_text('private in-root fixture')
            expected = digest(root)
            self.assertEqual(context.source_identity(root), expected)
            args = dict(mandatory_paths=['rules'], byte_budget=6000, max_file_bytes=100, source_digest=expected)
            bundle = context.retrieve_context(root, 'q', **args)
            self.assertEqual(bundle['files'][0]['content'].encode(), (root / 'rules').read_bytes())
            self.assertEqual({x['path']: x['reason'] for x in bundle['omitted']}, {'.env': 'sensitive', 'large': 'oversize'})
            original = context.read_text
            def drift(*args):
                result = original(*args)
                (root / 'new.py').write_text('source drift')
                return result
            with patch.object(context, 'read_text', side_effect=drift):
                with self.assertRaisesRegex(ValueError, 'after retrieval'):
                    context.retrieve_context(root, 'q', **args)

    def test_import_relation_and_metadata_overflow(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'main.py').write_text('from dependency import action\n')
            (root / 'dependency.py').write_text('def action(): return 1\n')
            bundle = context.retrieve_context(root, 'unmatched', mandatory_paths=['main.py'],
                byte_budget=2000, max_file_bytes=1000, source_digest=digest(root))
            self.assertEqual([e['path'] for e in bundle['files']], ['main.py', 'dependency.py'])
            for index in range(100):
                (root / ('irrelevant-%03d.txt' % index)).write_text('unrelated')
            with self.assertRaisesRegex(ValueError, 'omission metadata'):
                context.retrieve_context(root, 'q', mandatory_paths=['main.py'],
                    byte_budget=1000, max_file_bytes=1000, source_digest=digest(root))

    def test_packet_and_path_fences(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'rules').write_text('required')
            args = dict(mandatory_paths=['rules'], byte_budget=1000, max_file_bytes=1000, source_digest=digest(root))
            bundle = context.retrieve_context(root, 'q', **args)
            for paths in (['rules', 'rules'], ['../rules'], ['./rules'], ['rules//x'], ['/tmp/rules'], ['.git/HEAD']):
                with self.subTest(paths=paths), self.assertRaises((ValueError, OSError)):
                    context.retrieve_context(root, 'q', **dict(args, mandatory_paths=paths))
            for change in (lambda x: x['files'][0].update(mandatory=False),
                           lambda x: x['files'][0].update(bytes=True),
                           lambda x: x['omitted'].append({'path': 'rules', 'reason': 'budget'})):
                changed = copy.deepcopy(bundle)
                change(changed)
                with self.assertRaises(ValueError):
                    context.validate_context(changed, args['source_digest'], ['rules'], 1000)
            with tempfile.TemporaryDirectory() as outside:
                (Path(outside) / 'private').write_text('must never be opened')
                (root / 'linked').symlink_to(outside, target_is_directory=True)
                with self.assertRaises(ValueError):
                    context.retrieve_context(root, 'q', **args)

    def test_known_encoded_lease_values_fail_closed_without_rewriting(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'repo'
            root.mkdir()
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            skill = root / 'skills/agent-workflow/SKILL.md'
            skill.parent.mkdir(parents=True)
            benign = base64.b64encode(b'print("benign required content")').decode()
            skill.write_text('Required benign ' + benign)
            engine = Engine(Path(directory) / 'state', config(root))
            task = engine.import_task('sensitive-native-fixture', 'Known lease source fixture')
            handle = engine.claim('fixture', task['id'])
            token = handle['token']
            raw = token.encode()
            forms = [token, raw.hex(), base64.b64encode(raw).decode(),
                     ''.join('\\u%04x' % ord(c) for c in token),
                     ''.join('%%%02x' % c for c in raw),
                     base64.b64encode(json.dumps({'nested': token}).encode()).decode()]
            for form in forms:
                with self.subTest(form_kind=len(form)):
                    trace = root / 'trace.txt'
                    trace.write_text('repair observation: ' + form)
                    result = engine.context_query('repair')
                    self.assertEqual(result['context']['files'][0]['content'], skill.read_text())
                    self.assertEqual({x['path']: x['reason'] for x in result['context']['omitted']}['trace.txt'], 'sensitive')
                    with self.assertRaises(WorkflowError):
                        engine.context_query('repair', mandatory_paths='["trace.txt"]')
            skill.write_text('Required ' + forms[-1])
            with self.assertRaises(WorkflowError):
                engine.context_query('repair')
            engine.db.close()

    def test_core_rules_are_mandatory_and_query_is_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'repo'
            root.mkdir()
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            skill = root / 'skills/agent-workflow/SKILL.md'
            skill.parent.mkdir(parents=True)
            skill.write_text('frozen instructions ' + 'λ' * 900)
            (root / 'main.py').write_text('retrieval = 1\n')
            engine = Engine(Path(directory) / 'state', config(root))
            before = list(engine.db.iterdump())
            source = digest(root)
            result = engine.context_query('retrieval', mandatory_paths='["main.py"]', max_file_bytes='10')
            self.assertEqual(result['mandatory']['paths'], ['skills/agent-workflow/SKILL.md', 'main.py'])
            self.assertEqual(result['context']['files'][0]['content'], skill.read_text())
            for key in ('permissions', 'budget', 'acceptance', 'tests'):
                self.assertEqual(result['mandatory'][key], engine.config[key])
            self.assertEqual(result['used_bytes'], context.used_bytes(result))
            self.assertLessEqual(result['used_bytes'], 16384)
            self.assertEqual(before, list(engine.db.iterdump()))
            self.assertEqual(source, digest(root))
            for args in ({'byte_budget': '100'}, {'byte_budget': '65537'}, {'max_file_bytes': '65537'},
                         {'byte_budget': True}, {'mandatory_paths': '["../escape"]'},
                         {'mandatory_paths': '["main.py","main.py"]'}):
                with self.subTest(args=args), self.assertRaises(WorkflowError):
                    engine.context_query('q', **args)
            with self.assertRaises(TypeError):
                engine.context_query('q', root='/tmp')
            skill.unlink()
            with self.assertRaises(WorkflowError):
                engine.context_query('q', mandatory_paths='[]')

    def test_cli_and_mcp_same_read_only_adapter(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'repo'
            root.mkdir()
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            skill = root / 'skills/agent-workflow/SKILL.md'
            skill.parent.mkdir(parents=True)
            skill.write_text('Required local policy')
            (root / 'main.py').write_text('context = 1\n')
            state = Path(directory) / 'state'
            engine = Engine(state, config(root))
            before = list(engine.db.iterdump())
            base = [sys.executable, '-m', 'agent_workflow', '--state', str(state)]
            args = {'query': 'context', 'mandatory_paths': '["main.py"]'}
            cli = subprocess.run(base + ['context_query', '--args', json.dumps(args)], cwd=ROOT,
                                 text=True, capture_output=True, timeout=20)
            self.assertEqual(cli.returncode, 0, cli.stdout + cli.stderr)
            value = json.loads(cli.stdout)['result']
            requests = [
                {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {}},
                {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
                {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {'name': 'context_query', 'arguments': args}},
                {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {'name': 'context_query', 'arguments': dict(args, root='/tmp')}},
            ]
            mcp = subprocess.run(base + ['mcp'], input=''.join(json.dumps(r) + '\n' for r in requests),
                                 cwd=ROOT, text=True, capture_output=True, timeout=20)
            replies = [json.loads(line) for line in mcp.stdout.splitlines()]
            self.assertEqual(mcp.returncode, 0, mcp.stderr)
            self.assertFalse(replies[1]['result']['isError'])
            self.assertEqual(json.loads(replies[1]['result']['content'][0]['text']), value)
            self.assertTrue(replies[2]['result']['isError'])
            self.assertEqual(before, list(engine.db.iterdump()))
            engine.db.close()


if __name__ == '__main__':
    unittest.main()
