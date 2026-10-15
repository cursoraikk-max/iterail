import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from tests.test_core import config


class MCPTests(unittest.TestCase):
    def test_stdio_cli_shared_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / 'repo'
            repo.mkdir()
            subprocess.run(['git', 'init', '-q', str(repo)], check=True)
            cfg = Path(tmp) / 'config.json'
            cfg.write_text(json.dumps(config(repo)))
            state = str(Path(tmp) / 'state')
            base = [sys.executable, '-m', 'agent_workflow', '--state', state]
            subprocess.run(base + ['--config', str(cfg), 'init'], check=True, capture_output=True)
            p = subprocess.Popen(base + ['mcp'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                def rpc(i, method, params={}):
                    p.stdin.write(json.dumps({'jsonrpc': '2.0', 'id': i, 'method': method, 'params': params})+'\n')
                    p.stdin.flush()
                    return json.loads(p.stdout.readline())
                self.assertEqual(rpc(1, 'initialize', {'protocolVersion': '2025-06-18'})['result']['protocolVersion'], '2025-06-18')
                p.stdin.write('{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
                p.stdin.flush()
                self.assertGreater(len(rpc(2, 'tools/list')['result']['tools']), 5)
                created = rpc(3, 'tools/call', {'name': 'import', 'arguments': {'external_key': 'mcp', 'title': 'MCP task'}})
                task = json.loads(created['result']['content'][0]['text'])['id']
                cli = json.loads(subprocess.check_output(base + ['status', '--args', json.dumps({'task_id': task})], text=True))
                self.assertEqual(cli['result']['state'], 'queued')
                claimed = rpc(4, 'tools/call', {'name': 'claim', 'arguments': {'worker': 'mcp-test', 'task_id': task}})
                run = json.loads(claimed['result']['content'][0]['text'])
                args = {'run_id': run['id'], 'token': run['token']}
                for i, name in enumerate(['change', 'verify'], 5):
                    self.assertFalse(rpc(i, 'tools/call', {'name': name, 'arguments': args})['result']['isError'])
                for i, target in enumerate(['ready_review', 'accepted'], 7):
                    self.assertFalse(rpc(i, 'tools/call', {'name': 'transition', 'arguments': dict(args, target=target)})['result']['isError'])
                cli = json.loads(subprocess.check_output(base + ['status', '--args', json.dumps({'run_id': run['id']})], text=True))
                self.assertEqual(cli['result']['state'], 'accepted')
                forbidden = rpc(9, 'tools/call', {'name': 'transition', 'arguments': dict(args, target='published')})
                self.assertTrue(forbidden['result']['isError'])
                self.assertEqual(rpc(10, 'unknown')['error']['code'], -32601)
            finally:
                p.stdin.close()
                p.wait(timeout=10)
                self.assertEqual(p.stderr.read(), '')
                p.stdout.close()
                p.stderr.close()
