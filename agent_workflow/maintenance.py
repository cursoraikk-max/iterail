"""Owner-approved public source observations; no execution or provider authority."""
import copy
import base64
import hashlib
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import socket
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit
import urllib.request


class SourcePaused(ValueError):
    """An owner cancellation preserves due work rather than consuming a poll."""


def require(condition, message):
    if not condition:
        raise ValueError(message)


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'duplicate JSON member')
            result[key] = value
        return result
    def constant(value):
        raise ValueError('nonfinite JSON constant')
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def string(value):
    return isinstance(value, str) and 0 < len(value) <= 4096


def public_host(host):
    require(isinstance(host, str) and host == host.lower() and len(host) <= 253
            and re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', host)
            and '.' in host and '..' not in host
            and not host.endswith(('.localhost', '.local', '.internal', '.test', '.invalid')),
            'public official hostname required')
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError('IP literal is not an approved official source')
    require(all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]*[a-z0-9])?', x)
                for x in host.split('.')), 'invalid hostname')


def validate_config(value):
    require(isinstance(value, dict) and set(value) == {'schema_version', 'project', 'approved_hosts', 'checks'}
            and type(value['schema_version']) is int and value['schema_version'] == 1,
            'exact maintenance owner policy required')
    c = copy.deepcopy(value); project = c['project']
    require(isinstance(project, dict) and set(project) == {'id', 'purpose', 'goals', 'non_goals', 'needs'}
            and string(project['id']) and string(project['purpose']), 'project purpose required')
    for name in ('goals', 'non_goals'):
        xs = project[name]
        require(isinstance(xs, list) and len(xs) <= 64 and all(string(x) for x in xs)
                and len(xs) == len(set(xs)), 'unique project goals required')
    require(isinstance(project['needs'], list) and 0 < len(project['needs']) <= 64, 'project needs required')
    needs = set()
    for need in project['needs']:
        require(isinstance(need, dict) and set(need) == {'id', 'kind', 'name', 'current'}
                and need['kind'] in {'dependency', 'capability'}
                and all(string(need[x]) for x in ('id', 'name', 'current'))
                and need['id'] not in needs, 'invalid project need')
        needs.add(need['id'])
    hosts = c['approved_hosts']
    require(isinstance(hosts, list) and 0 < len(hosts) <= 64 and all(isinstance(x, str) for x in hosts)
            and len(hosts) == len(set(hosts)), 'fixed official hosts required')
    for host in hosts:
        public_host(host)
    require(isinstance(c['checks'], list) and 0 < len(c['checks']) <= 64, 'bounded maintenance checks required')
    ids = set()
    for check in c['checks']:
        require(isinstance(check, dict) and set(check) == {'id', 'need_id', 'source', 'expected_current',
                'interval_seconds', 'backoff_seconds', 'jitter_seconds', 'acceptance', 'authorization'}
                and string(check['id']) and check['id'] not in ids and check['need_id'] in needs,
                'invalid maintenance check')
        ids.add(check['id'])
        require(isinstance(check['expected_current'], str) and re.fullmatch('[0-9a-f]{64}', check['expected_current']),
                'current fingerprint required')
        for name in ('interval_seconds', 'backoff_seconds'):
            require(type(check[name]) is int and 0 < check[name] <= 31536000, 'positive bounded schedule required')
        require(check['backoff_seconds'] <= check['interval_seconds']
                and type(check['jitter_seconds']) is int and 0 <= check['jitter_seconds'] <= check['interval_seconds'],
                'bounded backoff and jitter required')
        source = check['source']
        require(isinstance(source, dict) and set(source) == {'url', 'max_bytes', 'format'}
                and isinstance(source['url'], str) and len(source['url']) <= 4096
                and type(source['max_bytes']) is int and 0 < source['max_bytes'] <= 1048576
                and source['format'] in {'capability_manifest', 'github_release', 'text_digest'}, 'invalid fixed source')
        url = urlsplit(source['url']); public_host(url.hostname)
        require(url.scheme == 'https' and url.hostname in hosts and url.username is None
                and url.password is None and url.port in (None, 443) and not url.fragment
                and not any(ord(x) < 32 or ord(x) == 127 for x in source['url']), 'approved HTTPS source required')
        require(check['acceptance'] == {'kind': 'version_or_deprecation'}, 'fixed maintenance acceptance required')
        auth = check['authorization']
        require(isinstance(auth, dict) and set(auth) == {'admit_task', 'allow_dependency_update'}
                and all(type(x) is bool for x in auth.values()), 'explicit maintenance authority required')
    return c


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('official source redirect rejected')


def _public_fetch_raw(url, max_bytes):
    """Credential-free HTTPS, no proxies/redirects, public DNS and bounded body."""
    host = urlsplit(url).hostname
    addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    require(addresses and all(ipaddress.ip_address(x[4][0]).is_global for x in addresses),
            'nonpublic source address rejected')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    request = urllib.request.Request(url, headers={'User-Agent': 'agent-workflow-maintenance/1',
                                                  'Accept': 'application/json,text/html,text/plain'})
    with opener.open(request, timeout=10) as response:
        body = response.read(max_bytes + 1)
        require(len(body) <= max_bytes, 'official response exceeds owner bound')
        return {'status': response.status, 'url': response.url,
                'content_type': response.headers.get('Content-Type', ''), 'body': body}


def _bounded_transport(argv, *, deadline=None, gate=None, timeout=10):
    """A slow DNS/server cannot extend the owner window or ignore stop."""
    cutoff = time.monotonic() + timeout
    if deadline is not None:
        cutoff = min(cutoff, time.monotonic() + max(0, deadline - time.time()))
    if time.monotonic() >= cutoff or (gate is not None and not gate()):
        raise SourcePaused('source owner window closed')
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        child = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=output, stderr=errors,
                                 start_new_session=True, close_fds=True,
                                 env={key: value for key, value in os.environ.items()
                                      if key in {'PATH', 'LANG', 'LC_ALL', 'TMPDIR', 'SSL_CERT_FILE', 'SSL_CERT_DIR'}})
        failure = None
        try:
            while child.poll() is None:
                if time.monotonic() >= cutoff or (gate is not None and not gate()):
                    failure = 'source owner window closed'; break
                if os.fstat(output.fileno()).st_size + os.fstat(errors.fileno()).st_size > 2097152:
                    failure = 'source transport output exceeds bound'; break
                time.sleep(0.025)
            if failure == 'source owner window closed':
                raise SourcePaused(failure)
            require(failure is None and child.returncode == 0, failure or 'official source transport failed')
            if time.monotonic() >= cutoff or (gate is not None and not gate()):
                raise SourcePaused('source owner window closed')
            require(os.fstat(output.fileno()).st_size + os.fstat(errors.fileno()).st_size <= 2097152,
                    'source transport output exceeds bound')
            output.seek(0); raw = output.read(2097153)
            require(len(raw) <= 2097152, 'source transport output exceeds bound')
            return strict_json(raw)
        finally:
            # Kill the group even after leader exit; transport may not leave descendants.
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(child.pid, sig)
                except ProcessLookupError:
                    break
                except PermissionError as error:
                    # Darwin can briefly deny a signal while a terminated group
                    # disappears. Only ESRCH confirms cleanup; live or unknown
                    # groups retain the original failure after this bounded grace.
                    grace = time.monotonic() + 0.1
                    absent = False
                    while time.monotonic() < grace:
                        try:
                            os.killpg(child.pid, 0)
                        except ProcessLookupError:
                            absent = True
                            break
                        except PermissionError:
                            pass
                        time.sleep(0.005)
                    if not absent:
                        raise error
                    break
                if sig == signal.SIGTERM:
                    try:
                        child.wait(timeout=0.1)
                    except subprocess.TimeoutExpired:
                        pass
            child.wait(timeout=1)


def public_fetch(url, max_bytes, *, deadline=None, gate=None):
    result = _bounded_transport([sys.executable, str(Path(__file__).resolve()),
                                 '--fetch', url, str(max_bytes)], deadline=deadline, gate=gate)
    require(isinstance(result, dict) and set(result) == {'status', 'url', 'content_type', 'body_base64'},
            'source transport protocol invalid')
    result['body'] = base64.b64decode(result.pop('body_base64'), validate=True)
    return result


def normalize(check, result):
    source = check['source']
    require(isinstance(result, dict) and set(result) == {'status', 'url', 'content_type', 'body'}
            and type(result['status']) is int and result['status'] == 200
            and result['url'] == source['url'] and isinstance(result['content_type'], str)
            and isinstance(result['body'], bytes) and len(result['body']) <= source['max_bytes'],
            'invalid bounded official response')
    raw = result['body']; text = raw.decode('utf-8')
    if source['format'] == 'text_digest':
        return {'version': 'sha256:' + hashlib.sha256(raw).hexdigest(), 'deprecated': False, 'notice': None}
    require(result['content_type'].split(';', 1)[0].strip().lower() == 'application/json', 'JSON content type required')
    data = strict_json(text)
    require(isinstance(data, dict), 'official JSON object required')
    if source['format'] == 'github_release':
        require(string(data.get('tag_name')) and (data.get('body') is None or isinstance(data.get('body'), str))
                and all(type(data.get(x)) is bool for x in ('draft', 'prerelease'))
                and all(x in data for x in ('tag_name', 'body', 'draft', 'prerelease')), 'invalid official release metadata')
        if data['draft'] or data['prerelease']:
            return None
        return {'version': data['tag_name'], 'deprecated': False, 'notice': data['body']}
    require(set(data) == {'schema_version', 'version', 'deprecated', 'notice'}
            and type(data['schema_version']) is int and data['schema_version'] == 1
            and string(data['version']) and type(data['deprecated']) is bool
            and (data['notice'] is None or isinstance(data['notice'], str)), 'invalid capability manifest')
    return {x: data[x] for x in ('version', 'deprecated', 'notice')}


class MaintenanceScheduler:
    def __init__(self, state, config=None, *, clock=time.time, fetch=None, gate=None, deadline=None):
        raw = Path(state); require(not raw.is_symlink(), 'maintenance state symlink')
        existed = raw.exists(); self.root = raw.resolve(); self.clock = clock
        self.fetch = fetch; self.gate = gate; self.deadline = deadline
        path = self.root / 'maintenance.sqlite'
        require(not path.is_symlink(), 'maintenance journal symlink')
        if not path.exists():
            require(not existed and config is not None, 'initialized maintenance history missing')
            owner = validate_config(config); now = self._now()
            self.root.mkdir(parents=True, mode=0o700)
            self.db = sqlite3.connect(path, isolation_level=None, timeout=15)
            self.db.executescript('CREATE TABLE policy(id INTEGER PRIMARY KEY CHECK(id=1),config TEXT,sha TEXT);'
                                 'CREATE TABLE checks(id TEXT PRIMARY KEY,state TEXT);'
                                 'CREATE TABLE findings(check_id TEXT,fingerprint TEXT,data TEXT,PRIMARY KEY(check_id,fingerprint));')
            self.db.execute('BEGIN IMMEDIATE')
            try:
                self.db.execute('INSERT INTO policy VALUES(1,?,?)', (encoded(owner).decode(), digest(owner)))
                for check in owner['checks']:
                    state = {'next_due': now, 'failures': 0, 'last_fingerprint': None,
                             'last_checked': None, 'last_status': 'due', 'seen': [], 'ignored': []}
                    self.db.execute('INSERT INTO checks VALUES(?,?)', (check['id'], encoded(state).decode()))
                self.db.execute('COMMIT')
            except BaseException:
                self.db.execute('ROLLBACK'); self.db.close(); raise
        else:
            self.db = sqlite3.connect('file:' + str(path) + '?mode=rw', uri=True, isolation_level=None, timeout=15)
        try:
            row = self.db.execute('SELECT config,sha FROM policy WHERE id=1').fetchone()
            require(row is not None, 'initialized maintenance policy missing')
            self.config = validate_config(strict_json(row[0]))
            require(digest(self.config) == row[1], 'maintenance owner policy changed')
            require(config is None or validate_config(config) == self.config, 'maintenance owner policy is immutable')
            self.config_sha = row[1]
            require({x[0] for x in self.db.execute('SELECT id FROM checks')} == {x['id'] for x in self.config['checks']},
                    'maintenance schedule history missing')
            self.status()
        except BaseException:
            self.db.close(); raise

    def _now(self):
        value = self.clock()
        require(type(value) in (int, float) and math.isfinite(value), 'finite clock required')
        return value

    def status(self):
        checks = {}
        for ident, raw in self.db.execute('SELECT id,state FROM checks ORDER BY rowid'):
            state = strict_json(raw)
            require(isinstance(state, dict) and type(state.get('failures')) is int and state['failures'] >= 0
                    and type(state.get('next_due')) in (int, float) and math.isfinite(state['next_due']),
                    'maintenance schedule corrupt')
            for key in ('seen', 'ignored'):
                require(isinstance(state.get(key), list) and len(state[key]) == len(set(state[key]))
                        and all(isinstance(x, str) and re.fullmatch('[0-9a-f]{64}', x) for x in state[key]),
                        'maintenance deduplication corrupt')
            checks[ident] = state
        return {'schema_version': 1, 'config': copy.deepcopy(self.config), 'config_sha256': self.config_sha, 'checks': checks}

    def ignore(self, check_id, fingerprint):
        require(check_id in {x['id'] for x in self.config['checks']} and isinstance(fingerprint, str)
                and re.fullmatch('[0-9a-f]{64}', fingerprint), 'known check and exact ignored fingerprint required')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            state = self.status()['checks'][check_id]
            if fingerprint not in state['ignored']:
                state['ignored'].append(fingerprint)
            self.db.execute('UPDATE checks SET state=? WHERE id=?', (encoded(state).decode(), check_id))
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK'); raise

    def tick(self, stopped=False, budget_available=True):
        require(type(stopped) is bool and type(budget_available) is bool, 'explicit owner gate required')
        result = {'schema_version': 1, 'paused': stopped or not budget_available, 'findings': [], 'checks': []}
        if result['paused']:
            for ident, state in self.status()['checks'].items():
                result['checks'].append({'id': ident, 'status': 'stopped' if stopped else 'budget_blocked', 'next_due': state['next_due']})
            return result
        self.db.execute('BEGIN IMMEDIATE')
        try:
            states = self.status()['checks']
            for check in self.config['checks']:
                ident = check['id']; state = states[ident]; now = self._now(); status = 'not_due'
                if self.gate is not None and not self.gate():
                    result['paused'] = True; status = 'budget_blocked'
                elif now >= state['next_due']:
                    jitter = int(hashlib.sha256(ident.encode()).hexdigest()[:8], 16) % (check['jitter_seconds'] + 1)
                    try:
                        if self.fetch is None:
                            deadline = self.deadline() if callable(self.deadline) else self.deadline
                            response = public_fetch(check['source']['url'], check['source']['max_bytes'],
                                                    deadline=deadline, gate=self.gate)
                        else:
                            response = self.fetch(check['source']['url'], check['source']['max_bytes'])
                        if self.gate is not None and not self.gate():
                            raise SourcePaused('source owner window closed')
                        observation = normalize(check, response)
                    except SourcePaused:
                        result['paused'] = True
                        status = 'budget_blocked'
                    except Exception as error:
                        state['failures'] += 1
                        delay = min(check['interval_seconds'], check['backoff_seconds'] * 2 ** min(state['failures'] - 1, 30))
                        state.update(next_due=now + delay + jitter, last_checked=now,
                                     last_status='source_failure', source_error=type(error).__name__)
                        status = 'source_failure'
                    else:
                        state.update(failures=0, next_due=now + check['interval_seconds'] + jitter,
                                     last_checked=now, source_error=None)
                        status = 'skipped'
                        if observation is not None:
                            fingerprint = digest({x: observation[x] for x in ('version', 'deprecated')})
                            state['last_fingerprint'] = fingerprint
                            status = 'unchanged' if fingerprint == check['expected_current'] else 'changed'
                            if fingerprint in state['ignored']:
                                status = 'ignored'
                            elif status == 'changed' and fingerprint not in state['seen'] and check['authorization']['admit_task']:
                                finding = {'check_id': ident, 'project_id': self.config['project']['id'],
                                           'need_id': check['need_id'], 'kind': 'deprecation' if observation['deprecated'] else 'update',
                                           'fingerprint': fingerprint, 'version': observation['version'],
                                           'deprecated': observation['deprecated'], 'source_url': check['source']['url'],
                                           'acceptance': copy.deepcopy(check['acceptance']),
                                           'authorization': copy.deepcopy(check['authorization']),
                                           'notice': observation['notice'][:4096] if observation['notice'] is not None else None,
                                           'observed_at': now, 'evidence_kind': 'public_source_observation',
                                           'response_sha256': hashlib.sha256(response['body']).hexdigest()}
                                self.db.execute('INSERT INTO findings VALUES(?,?,?)', (ident, fingerprint, encoded(finding).decode()))
                                state['seen'].append(fingerprint); result['findings'].append(finding)
                        state['last_status'] = status
                    self.db.execute('UPDATE checks SET state=? WHERE id=?', (encoded(state).decode(), ident))
                result['checks'].append({'id': ident, 'status': status, 'next_due': state['next_due']})
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK'); raise
        return result


if __name__ == '__main__':
    if len(sys.argv) != 4 or sys.argv[1] != '--fetch':
        raise SystemExit('Internal bounded public-source transport only')
    answer = _public_fetch_raw(sys.argv[2], int(sys.argv[3]))
    answer['body_base64'] = base64.b64encode(answer.pop('body')).decode('ascii')
    print(json.dumps(answer, ensure_ascii=False))
