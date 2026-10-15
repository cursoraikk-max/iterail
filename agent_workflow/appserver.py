"""Offline semantic projection of the stable Codex 0.162.1 app-server wire.

This module observes supplied data. It cannot send requests, execute approvals,
launch a backend, or grant workflow acceptance. It is not a full schema validator.
"""
import argparse
import copy
import json
from pathlib import Path
import sys


PROTOCOL_VERSION = '0.162.1'
SCHEMA_SHA256 = '69b19c71c7707c369ea913c0b4ad1e96bf5ed49e0f4606d70677d39b8fc0d638'
MAX_BYTES = 65536
APPROVALS = {'item/commandExecution/requestApproval', 'item/fileChange/requestApproval'}
METHODS = {'thread/started', 'turn/started', 'turn/completed', 'item/started',
           'item/completed', 'item/agentMessage/delta', 'error', 'thread/tokenUsage/updated'} | APPROVALS
REQUESTS = {'initialize', 'thread/read', 'thread/start', 'turn/start'}
ITEM_TYPES = {'userMessage', 'hookPrompt', 'agentMessage', 'functionCallOutput', 'plan',
              'reasoning', 'commandExecution', 'fileChange', 'mcpToolCall', 'dynamicToolCall',
              'collabAgentToolCall', 'webSearch', 'imageView', 'imageGeneration',
              'enteredReviewMode', 'exitedReviewMode', 'contextCompaction'}


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _identifier(value):
    return isinstance(value, str) and bool(value)


def _request_id(value):
    return (type(value) is int and -(2 ** 63) <= value < 2 ** 63) or _identifier(value)


def _members(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, 'duplicate JSON member')
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError('nonfinite JSON number')


class OfflineSession:
    """Atomic, bounded lifecycle observation; model text has no authority."""

    def __init__(self, protocol_version=PROTOCOL_VERSION, max_bytes=MAX_BYTES):
        _require(protocol_version == PROTOCOL_VERSION, 'unsupported protocol version')
        _require(type(max_bytes) is int and 0 < max_bytes <= MAX_BYTES, 'invalid byte bound')
        self.max_bytes = max_bytes
        self._state = {'schema_version': 1, 'protocol_version': protocol_version,
                       'schema_sha256': SCHEMA_SHA256, 'completed': False, 'failed': False,
                       'thread_ids': [], 'turn_ids': [], 'active_thread': None,
                       'active_turn': None, 'open_items': {}, 'seen_items': [], 'pending_approvals': [],
                       'requests': {}, 'response_ids': [], 'used_bytes': 0, 'events': 0}

    def snapshot(self):
        result = copy.deepcopy(self._state)
        result['requests'] = [{'id': key[1], 'method': value} for key, value in result['requests'].items()]
        result['response_ids'] = [key[1] for key in result['response_ids']]
        return result

    def register_request(self, id, method):
        _require(_request_id(id), 'invalid request ID')
        _require(isinstance(method, str) and method in REQUESTS, 'unsupported correlation method')
        _require(not isinstance(id, str) or len(id) <= self.max_bytes, 'request metadata byte bound exceeded')
        key = (type(id).__name__, id)
        _require(key not in self._state['requests'], 'duplicate request ID')
        _require(len(self._state['requests']) < 128, 'request correlation capacity')
        try:
            metadata = json.dumps({'id': id, 'method': method}, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
            count = len(metadata.encode('utf-8'))
        except UnicodeError as error:
            raise ValueError('invalid request metadata') from error
        _require(count + self._state['used_bytes'] <= self.max_bytes, 'request metadata byte bound exceeded')
        self._state['requests'][key] = method
        self._state['used_bytes'] += count

    def consume(self, message):
        try:
            if isinstance(message, str):
                count = len(message.encode('utf-8'))
                _require(count + self._state['used_bytes'] <= self.max_bytes, 'input byte bound exceeded')
                event = json.loads(message, object_pairs_hook=_members, parse_constant=_invalid_constant)
            elif isinstance(message, dict):
                wire = json.dumps(message, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
                count = len(wire.encode('utf-8'))
                _require(count + self._state['used_bytes'] <= self.max_bytes, 'input byte bound exceeded')
                event = copy.deepcopy(message)
            else:
                raise ValueError('object or JSON string required')
            state = copy.deepcopy(self._state)
            observation = self._observe(state, event)
        except (RecursionError, UnicodeError) as error:
            raise ValueError('invalid bounded JSON input') from error
        state['used_bytes'] += count
        state['events'] += 1
        self._state = state
        return observation

    def _observe(self, state, event):
        _require(isinstance(event, dict), 'invalid envelope')
        _require('jsonrpc' not in event or event['jsonrpc'] == '2.0', 'unsupported JSON-RPC version')
        base = {'kind': 'notification', 'method': None, 'request_id': None,
                'thread_id': None, 'turn_id': None, 'terminal': False, 'status': None}
        if 'method' not in event:
            _require(set(event) <= {'jsonrpc', 'id', 'result', 'error'} and 'id' in event,
                     'invalid response envelope')
            _require(('result' in event) != ('error' in event), 'response must have result or error')
            ident = event['id']
            _require(_request_id(ident), 'invalid response ID')
            key = (type(ident).__name__, ident)
            _require(key in state['requests'] and key not in state['response_ids'], 'unknown or duplicate response')
            if 'error' in event:
                error = event['error']
                _require(isinstance(error, dict) and type(error.get('code')) is int
                         and isinstance(error.get('message'), str), 'invalid response error')
                state['failed'] = True
                state['completed'] = False
            state['response_ids'].append(key)
            return dict(base, kind='error' if 'error' in event else 'response',
                        method=state['requests'][key], request_id=ident,
                        status='failed' if 'error' in event else 'observed')
        method = event['method']
        _require(isinstance(method, str) and method in METHODS, 'unsupported observation method')
        _require(set(event) <= {'jsonrpc', 'id', 'method', 'params'} and isinstance(event.get('params'), dict),
                 'invalid notification envelope')
        _require(('id' in event) == (method in APPROVALS), 'invalid request/notification envelope')
        params = event['params']
        base['method'] = method
        if method == 'thread/started':
            thread = params.get('thread')
            _require(isinstance(thread, dict) and _identifier(thread.get('id')), 'invalid thread identity')
            ident = thread['id']
            _require(ident not in state['thread_ids'] and state['active_turn'] is None, 'duplicate or out-of-order thread')
            state['thread_ids'].append(ident)
            return dict(base, thread_id=ident, status='started')
        thread = params.get('threadId')
        _require(_identifier(thread), 'thread identity required')
        base['thread_id'] = thread
        if method == 'turn/started':
            turn = params.get('turn')
            _require(isinstance(turn, dict) and _identifier(turn.get('id'))
                     and turn.get('status') == 'inProgress' and isinstance(turn.get('items'), list), 'invalid starting turn')
            _require(state['active_turn'] is None and turn['id'] not in state['turn_ids'], 'duplicate or active turn')
            _require(not turn['items'], 'starting projection requires empty item lifecycle')
            if thread not in state['thread_ids']:
                state['thread_ids'].append(thread)
            state.update(active_thread=thread, active_turn=turn['id'], completed=False)
            state['turn_ids'].append(turn['id'])
            return dict(base, turn_id=turn['id'], status='inProgress')
        _require(state['active_turn'] is not None and thread == state['active_thread'], 'wrong or inactive thread')
        turn_id = params.get('turn', {}).get('id') if method == 'turn/completed' and isinstance(params.get('turn'), dict) else params.get('turnId')
        _require(turn_id == state['active_turn'], 'wrong or inactive turn')
        base['turn_id'] = turn_id
        if method == 'turn/completed':
            turn = params['turn']
            status = turn.get('status')
            _require(status in {'completed', 'failed', 'interrupted'} and isinstance(turn.get('items'), list), 'invalid terminal turn')
            if status == 'completed':
                _require(not state['open_items'] and not state['pending_approvals'] and not state['failed'],
                         'cannot complete with unresolved items, approvals or failure')
                state['completed'] = True
            else:
                state['failed'] = True
            state.update(active_thread=None, active_turn=None)
            return dict(base, terminal=True, status=status)
        if method in {'item/started', 'item/completed'}:
            item = params.get('item')
            _require(isinstance(item, dict) and _identifier(item.get('id')) and item.get('type') in ITEM_TYPES,
                     'invalid item identity or type')
            time_key = 'startedAtMs' if method == 'item/started' else 'completedAtMs'
            _require(type(params.get(time_key)) is int and params[time_key] >= 0, 'invalid item timestamp')
            ident = item['id']
            if method == 'item/started':
                _require(ident not in state['seen_items'], 'duplicate item start')
                state['open_items'][ident] = item['type']
                state['seen_items'].append(ident)
            else:
                _require(state['open_items'].get(ident) == item['type'], 'item completion without matching start')
                del state['open_items'][ident]
            return dict(base, status='started' if method == 'item/started' else 'completed')
        if method == 'item/agentMessage/delta':
            _require(state['open_items'].get(params.get('itemId')) == 'agentMessage'
                     and isinstance(params.get('delta'), str), 'delta without active agent message')
            return dict(base, status='delta')
        if method in APPROVALS:
            ident = event['id']
            _require(_request_id(ident) and ident not in state['pending_approvals'] and _identifier(params.get('itemId')),
                     'invalid or duplicate approval request')
            _require(type(params.get('startedAtMs')) is int and params['startedAtMs'] >= 0, 'invalid approval timestamp')
            state['pending_approvals'].append(ident)
            return dict(base, kind='approval_requested', request_id=ident, status='pending')
        if method == 'error':
            _require(isinstance(params.get('error'), dict) and isinstance(params['error'].get('message'), str)
                     and type(params.get('willRetry')) is bool, 'invalid lifecycle error')
            if not params['willRetry']:
                state['failed'] = True
            return dict(base, kind='error', status='retrying' if params['willRetry'] else 'failed')
        usage = params.get('tokenUsage')
        _require(isinstance(usage, dict), 'invalid token usage')
        for key in ('last', 'total'):
            values = usage.get(key)
            _require(isinstance(values, dict) and all(type(values.get(k)) is int and values[k] >= 0
                     for k in ('cachedInputTokens', 'inputTokens', 'outputTokens', 'reasoningOutputTokens', 'totalTokens')),
                     'invalid token usage counters')
        return dict(base, status='usage_observed')


def main():
    parser = argparse.ArgumentParser(description='Replay supplied app-server JSONL offline; never execute it')
    parser.add_argument('input', type=Path)
    parser.add_argument('--protocol-version', default=PROTOCOL_VERSION)
    parser.add_argument('--max-bytes', type=int, default=MAX_BYTES)
    parser.add_argument('--requests', default='[]', help='JSON list of local {id,method} correlation records')
    args = parser.parse_args()
    try:
        session = OfflineSession(args.protocol_version, args.max_bytes)
        requests = json.loads(args.requests, object_pairs_hook=_members, parse_constant=_invalid_constant)
        _require(isinstance(requests, list), 'request list required')
        for request in requests:
            _require(isinstance(request, dict) and set(request) == {'id', 'method'}, 'invalid correlation record')
            session.register_request(request['id'], request['method'])
        with args.input.open('rb') as stream:
            data = stream.read(args.max_bytes + 1)
        _require(len(data) <= args.max_bytes, 'input byte bound exceeded')
        observations = [session.consume(line) for line in data.decode('utf-8').splitlines(keepends=True) if line.strip()]
        snapshot = session.snapshot()
        print(json.dumps({'ok': True, 'observations': observations, 'snapshot': snapshot}, ensure_ascii=False))
    except (ValueError, TypeError, OSError) as error:
        print(json.dumps({'ok': False, 'error': str(error)}))
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
