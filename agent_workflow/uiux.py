"""Optional owner-bound UI investigations. Submitted evidence is never real capture proof."""
import copy
import contextlib
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import struct
import threading
import time
import zlib


def require(value, message):
    if not value:
        raise ValueError(message)


class UIUXIntegrityError(ValueError):
    """Immutable owner history or scope cannot be admitted for retry."""


@contextlib.contextmanager
def integrity_boundary():
    """Type validation failures only within persisted identity/scope checks."""
    try:
        yield
    except UIUXIntegrityError:
        raise
    except (ValueError, OSError, sqlite3.DatabaseError, KeyError, TypeError) as error:
        raise UIUXIntegrityError(str(error)) from error


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()


def canonical(value):
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


def text(value):
    return isinstance(value, str) and bool(value.strip()) and len(value.encode()) <= 4096


def hash_value(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def read_regular(path, limit):
    require(not path.is_symlink() and stat.S_ISREG(path.lstat().st_mode), 'regular owner file required')
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    with os.fdopen(fd, 'rb') as stream:
        require(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), 'regular owner file required')
        data = stream.read(limit + 1)
    require(len(data) <= limit, 'owner file byte bound exceeded')
    return data


def validate_png(data, width, height):
    """Validate a static PNG without retaining its decompressed image.

    All standard sample depths/color types and Adam7 passes are supported.
    The decoded scanlines must fit both the owner viewport and a 64 MiB
    safety ceiling; each inflate operation and buffered row is bounded.
    """
    require(data.startswith(b'\x89PNG\r\n\x1a\n'), 'PNG screenshot required')
    offset, header, palette, transparency = 8, None, None, False
    idat_start, idat_end, idat_closed = None, None, False
    seen = set()
    while offset < len(data):
        require(offset + 12 <= len(data), 'truncated PNG chunk')
        size = struct.unpack_from('!I', data, offset)[0]
        kind = data[offset+4:offset+8];end = offset + 12 + size
        require(size <= 0x7fffffff and end <= len(data)
                and all(65 <= x <= 90 or 97 <= x <= 122 for x in kind)
                and 65 <= kind[2] <= 90, 'invalid PNG chunk')
        body = memoryview(data)[offset+8:end-4]
        require(zlib.crc32(memoryview(data)[offset+4:end-4]) & 0xffffffff
                == struct.unpack_from('!I', data, end-4)[0], 'invalid PNG CRC')
        if header is None:
            require(kind == b'IHDR' and size == 13, 'PNG header missing')
            w, h, depth, color, compression, filtering, interlace = struct.unpack('!IIBBBBB', body)
            require((w, h) == (width, height), 'screenshot dimensions differ from owner viewport')
            depths = {0: (1, 2, 4, 8, 16), 2: (8, 16), 3: (1, 2, 4, 8), 4: (8, 16), 6: (8, 16)}
            require(color in depths and depth in depths[color] and compression == 0
                    and filtering == 0 and interlace in (0, 1), 'unsupported PNG format')
            header = (depth, color, interlace)
        elif kind == b'IHDR':
            raise ValueError('duplicate PNG header')
        elif kind == b'PLTE':
            require(palette is None and idat_start is None and not transparency and b'bKGD' not in seen
                    and color in (2, 3, 6) and 0 < size <= 768 and size % 3 == 0,
                    'invalid PNG palette')
            palette = size // 3
            require(color != 3 or palette <= 1 << depth, 'PNG palette exceeds sample depth')
        elif kind == b'IDAT':
            require(not idat_closed and (color != 3 or palette is not None), 'invalid PNG image data order')
            if idat_start is None: idat_start = offset
            idat_end = end
        elif kind == b'IEND':
            require(size == 0 and end == len(data) and idat_start is not None, 'invalid PNG end or missing image data')
            offset = end
            break
        else:
            require(kind[0] & 32 and kind not in {b'acTL', b'fcTL', b'fdAT'}, 'unsupported PNG critical or animation chunk')
            if idat_start is not None: idat_closed = True
            if kind in {b'cHRM', b'gAMA', b'iCCP', b'sBIT', b'sRGB', b'bKGD', b'hIST', b'tRNS', b'pHYs', b'tIME', b'eXIf'}:
                require(kind not in seen, 'duplicate PNG ancillary chunk')
                seen.add(kind)
            if kind in {b'cHRM', b'gAMA', b'iCCP', b'sBIT', b'sRGB'}:
                require(palette is None and idat_start is None, 'invalid PNG color metadata order')
            if kind in {b'bKGD', b'hIST', b'tRNS', b'pHYs'}:
                require(idat_start is None, 'invalid PNG ancillary chunk order')
            if kind == b'tRNS':
                transparency = True
                require((color == 0 and size == 2 and struct.unpack('!H', body)[0] < 1 << depth)
                        or (color == 2 and size == 6 and all(x < 1 << depth for x in struct.unpack('!HHH', body)))
                        or (color == 3 and palette is not None and 0 < size <= palette), 'invalid PNG transparency')
            if kind == b'bKGD':
                require((color in (0, 4) and size == 2 and struct.unpack('!H', body)[0] < 1 << depth)
                        or (color in (2, 6) and size == 6 and all(x < 1 << depth for x in struct.unpack('!HHH', body)))
                        or (color == 3 and palette is not None and size == 1 and body[0] < palette), 'invalid PNG background')
            if kind == b'hIST': require(palette is not None and size == 2 * palette, 'invalid PNG histogram')
            if kind == b'cHRM': require(size == 32, 'invalid PNG chromaticities')
            if kind == b'gAMA': require(size == 4 and struct.unpack('!I', body)[0] > 0, 'invalid PNG gamma')
            if kind == b'sRGB': require(size == 1 and body[0] <= 3 and b'iCCP' not in seen, 'invalid PNG sRGB')
            if kind == b'iCCP':
                profile = bytes(body);split = profile.find(b'\0')
                require(1 <= split <= 79 and len(profile) > split + 2 and profile[split+1] == 0
                        and b'sRGB' not in seen, 'invalid PNG profile')
            if kind == b'sBIT':
                count = {0: 1, 2: 3, 3: 3, 4: 2, 6: 4}[color]
                require(size == count and all(0 < x <= (8 if color == 3 else depth) for x in body), 'invalid PNG significant bits')
            if kind == b'pHYs': require(size == 9 and body[8] <= 1, 'invalid PNG pixel dimensions')
            if kind == b'tIME': require(size == 7, 'invalid PNG timestamp')
        offset = end
    require(header is not None and offset == len(data) and kind == b'IEND', 'PNG end missing')

    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color]
    bits = channels * depth
    passes = [(0, 0, 1, 1)] if interlace == 0 else [(0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8),
            (2, 0, 4, 4), (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2)]
    layout = []
    for x, y, dx, dy in passes:
        pw, ph = max(0, (width-x+dx-1)//dx), max(0, (height-y+dy-1)//dy)
        if pw and ph: layout.append((pw, ph, (pw * bits + 7)//8))
    expected = sum(ph * (row_bytes + 1) for _, ph, row_bytes in layout)
    require(0 < expected <= 64 * 1024 * 1024, 'PNG decoded image byte bound exceeded')

    def rows():
        for pw, ph, row_bytes in layout:
            for row in range(ph): yield pw, row_bytes, row == 0
    row_specs = iter(rows());spec = next(row_specs, None)
    buffered, previous, decoded = bytearray(), None, 0
    inflater = zlib.decompressobj()
    offset = idat_start
    try:
        while offset < idat_end:
            size = struct.unpack_from('!I', data, offset)[0]
            pos, stop = offset + 8, offset + 8 + size
            while pos < stop:
                pending = memoryview(data)[pos:min(pos+65536, stop)];pos += len(pending)
                while pending:
                    require(not inflater.eof, 'trailing PNG compressed data')
                    block = inflater.decompress(pending, min(65536, expected-decoded+1))
                    pending = inflater.unconsumed_tail
                    require(not inflater.unused_data, 'trailing PNG compressed data')
                    decoded += len(block)
                    require(decoded <= expected, 'PNG decoded image byte bound exceeded')
                    consumed = 0
                    while consumed < len(block):
                        require(spec is not None, 'excess PNG scanline data')
                        pw, row_bytes, first = spec
                        count = min(row_bytes + 1 - len(buffered), len(block) - consumed)
                        buffered.extend(block[consumed:consumed+count]);consumed += count
                        if len(buffered) != row_bytes + 1: continue
                        filter_byte = buffered[0]
                        require(filter_byte <= 4, 'invalid PNG scanline filter')
                        if color == 3:
                            # Indexed pixels must also refer to an existing palette entry.
                            row = buffered[1:]
                            if first: previous = bytearray(row_bytes)
                            for i in range(row_bytes):
                                left = row[i-1] if i else 0;above = previous[i];corner = previous[i-1] if i else 0
                                if filter_byte == 1: predictor = left
                                elif filter_byte == 2: predictor = above
                                elif filter_byte == 3: predictor = (left + above)//2
                                elif filter_byte == 4:
                                    p = left + above - corner
                                    distances = (abs(p-left), abs(p-above), abs(p-corner))
                                    predictor = (left, above, corner)[distances.index(min(distances))]
                                else: predictor = 0
                                row[i] = (row[i] + predictor) & 255
                            mask = (1 << depth)-1
                            require(all((row[(i*depth)//8] >> (8-depth-(i*depth)%8)) & mask < palette for i in range(pw)), 'PNG pixel outside palette')
                            previous = row
                        buffered.clear();spec = next(row_specs, None)
            offset += 12 + size
    except zlib.error as error:
        raise ValueError('invalid PNG compressed image stream') from error
    require(inflater.eof and not inflater.unused_data and decoded == expected and spec is None and not buffered,
            'incomplete PNG image stream')


def strings(value, allow_empty=True):
    return isinstance(value, list) and len(value) <= 64 and (allow_empty or bool(value)) and all(text(x) for x in value) and len(value) == len(set(value))


def validate_policy(value):
    require(isinstance(value, dict) and set(value) == {'schema_version', 'project', 'artifact_root', 'standards', 'cadence', 'limits', 'rubric', 'approved_brand_changes'}
            and type(value['schema_version']) is int and value['schema_version'] == 1, 'exact UIUX owner policy required')
    p = copy.deepcopy(value)
    require(isinstance(p['project'], dict) and set(p['project']) == {'id', 'has_ui'} and text(p['project']['id']) and type(p['project']['has_ui']) is bool, 'UIUX project identity required')
    root = Path(p['artifact_root']) if isinstance(p['artifact_root'], str) else Path('.')
    require(root.is_absolute() and root.is_dir() and not root.is_symlink(), 'owned artifact root directory required')
    s = p['standards']
    require(isinstance(s, dict) and set(s) == {'users', 'flows', 'references', 'tokens', 'viewports', 'a11y'}, 'exact owner UI standards required')
    for name in ('users', 'a11y'):
        require(strings(s[name], not p['project']['has_ui']), 'bounded unique UI standards required')
    require(isinstance(s['tokens'], dict) and len(s['tokens']) <= 128 and all(text(k) and text(v) for k, v in s['tokens'].items()), 'bounded owner design tokens required')
    for name, fields in (('flows', {'id', 'steps'}), ('references', {'id', 'uri'}), ('viewports', {'id', 'width', 'height'})):
        rows = s[name]
        require(isinstance(rows, list) and len(rows) <= 64 and (name == 'references' or not p['project']['has_ui'] or rows), 'bounded UI scenarios required')
        ids = set()
        for item in rows:
            require(isinstance(item, dict) and set(item) == fields and text(item['id']) and item['id'] not in ids, 'unique UI standard ID required')
            ids.add(item['id'])
            if name == 'flows': require(strings(item['steps'], False), 'bounded owner flow steps required')
            if name == 'references': require(text(item['uri']), 'owner reference URI required')
            if name == 'viewports': require(all(type(item[k]) is int and 0 < item[k] <= 16384 for k in ('width', 'height')), 'bounded viewport required')
    require(isinstance(p['cadence'], dict) and set(p['cadence']) == {'interval_seconds'} and type(p['cadence']['interval_seconds']) is int and 0 < p['cadence']['interval_seconds'] <= 31536000, 'bounded UI cadence required')
    require(isinstance(p['limits'], dict) and set(p['limits']) == {'artifact_bytes', 'evidence_items'}
            and type(p['limits']['artifact_bytes']) is int and 0 < p['limits']['artifact_bytes'] <= 16777216
            and type(p['limits']['evidence_items']) is int and 0 < p['limits']['evidence_items'] <= 512, 'bounded UI evidence limits required')
    require(isinstance(p['rubric'], list) and len(p['rubric']) <= 64 and (not p['project']['has_ui'] or p['rubric']), 'owner rubric required')
    ids = set()
    for item in p['rubric']:
        require(isinstance(item, dict) and set(item) == {'id', 'minimum_score'} and text(item['id']) and item['id'] not in ids
                and type(item['minimum_score']) is int and 0 <= item['minimum_score'] <= 4, 'bounded unique owner rubric required')
        ids.add(item['id'])
    require(strings(p['approved_brand_changes']), 'fixed owner brand approvals required')
    return p


validate_config = validate_policy


def source_digest(root):
    root = Path(root)
    require(root.is_absolute() and root.is_dir() and not root.is_symlink(), 'UI source directory required')
    h = hashlib.sha256()
    for path in sorted(root.rglob('*')):
        rel = path.relative_to(root)
        if any(x in {'.git', '.workflow', '__pycache__'} for x in rel.parts) or path.suffix == '.pyc': continue
        require(not path.is_symlink(), 'source symlink unsupported')
        if path.is_file():
            h.update(str(rel).encode() + b'\0')
            with path.open('rb') as stream:
                while chunk := stream.read(65536): h.update(chunk)
            h.update(b'\0')
    return h.hexdigest()


class UIUXStore:
    def __init__(self, state, policy=None, clock=time.time, task_sink=None):
        self.db = None
        try:
            self._initialize(state, policy, clock, task_sink)
        except BaseException:
            if self.db is not None: self.db.close()
            raise

    @integrity_boundary()
    def _initialize(self, state, policy, clock, task_sink):
        self.root = Path(state)
        require(not self.root.is_symlink(), 'UIUX state symlink unsupported')
        existed = (self.root / 'uiux.sqlite').exists()
        require(existed or not self.root.exists() or not any(self.root.iterdir()), 'initialized UIUX history missing')
        require(policy is not None or existed, 'owner UIUX policy required for initialization')
        p = validate_policy(policy) if policy is not None else None
        self.root.mkdir(parents=True, exist_ok=True)
        self.root = self.root.resolve()
        require(not (self.root/'uiux.sqlite').is_symlink(), 'UIUX ledger symlink unsupported')
        self.clock, self.task_sink = clock, task_sink
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.root/'uiux.sqlite', isolation_level=None, timeout=10, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        if existed:
            tables = {x[0] for x in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            require({'meta', 'runs', 'evidence', 'reviews', 'proposals'} <= tables and (self.root/'identity.json').is_file()
                    and not (self.root/'identity.json').is_symlink(), 'initialized UIUX database history missing')
        if not existed: self.db.executescript('''CREATE TABLE meta(id INTEGER PRIMARY KEY,policy TEXT,policy_sha256 TEXT);
            CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY,source_root TEXT,source_sha256 TEXT,created REAL,next_due REAL,phase TEXT);
            CREATE TABLE IF NOT EXISTS evidence(run_id TEXT,id TEXT,stage TEXT,body TEXT,PRIMARY KEY(run_id,id));
            CREATE TABLE IF NOT EXISTS reviews(run_id TEXT,sha TEXT,body TEXT,phase TEXT,PRIMARY KEY(run_id,sha));
            CREATE TABLE IF NOT EXISTS proposals(external_key TEXT PRIMARY KEY,run_id TEXT,review_sha TEXT,body TEXT,blocked INTEGER,exported INTEGER);''')
        row = self.db.execute('SELECT * FROM meta WHERE id=1').fetchone()
        if row is None:
            require(not existed and p is not None, 'UIUX policy history missing')
            self.db.execute('INSERT INTO meta VALUES(1,?,?)', (encoded(p).decode(), canonical(p)))
            self.policy = p
        else:
            self.policy = validate_policy(strict_json(row['policy']))
            require(canonical(self.policy) == row['policy_sha256'] and (p is None or p == self.policy), 'owner UIUX policy is immutable')
        self.policy_sha256 = canonical(self.policy)
        identity = {'schema_version': 1, 'policy_sha256': self.policy_sha256}
        if existed:
            require(strict_json(read_regular(self.root/'identity.json', 4096)) == identity, 'UIUX owner identity changed')
        else:
            (self.root/'identity.json').write_bytes(encoded(identity))
        self.artifact_root = Path(self.policy['artifact_root']).resolve()
        info = (self.root/'uiux.sqlite').stat()
        self.database_identity = (info.st_dev, info.st_ino)

    @integrity_boundary()
    def _integrity(self):
        db_path = self.root/'uiux.sqlite'
        require(db_path.is_file() and not db_path.is_symlink() and not self.root.is_symlink(), 'initialized UIUX database history missing')
        info = db_path.stat()
        require((info.st_dev, info.st_ino) == self.database_identity, 'UIUX database replaced')
        row = self.db.execute('SELECT * FROM meta WHERE id=1').fetchone()
        require(row and row['policy_sha256'] == self.policy_sha256 and row['policy'] == encoded(self.policy).decode()
                and canonical(self.policy) == self.policy_sha256
                and strict_json(read_regular(self.root/'identity.json', 4096)) == {'schema_version': 1, 'policy_sha256': self.policy_sha256}, 'UIUX policy history changed')

    def _now(self):
        now = self.clock()
        require(type(now) in (float, int) and math.isfinite(now), 'finite UI clock required')
        return now

    def _run(self, ident):
        row = self.db.execute('SELECT * FROM runs WHERE id=?', (ident,)).fetchone()
        require(row is not None, 'unknown UIUX run')
        return dict(row)

    def _artifact(self, packet):
        artifact = packet['artifact'];configured = Path(self.policy['artifact_root']);root = self.artifact_root;raw = Path(artifact['path'])
        require(configured.is_dir() and not configured.is_symlink() and configured.resolve() == root, 'owner artifact root changed')
        require(raw.is_absolute() and raw.is_relative_to(root) and not raw.is_symlink() and raw.resolve().is_relative_to(root), 'artifact outside owner root or symlink')
        rel = raw.relative_to(root)
        candidate = root
        for part in rel.parts:
            candidate = candidate / part
            require(not candidate.is_symlink(), 'artifact symlink unsupported')
        data = read_regular(candidate, self.policy['limits']['artifact_bytes'])
        require(hashlib.sha256(data).hexdigest() == artifact['sha256'], 'artifact hash changed')
        return data

    def _fresh(self, run):
        self._integrity()
        require(source_digest(Path(run['source_root'])) == run['source_sha256'], 'stale UIUX source; discover a fresh run')
        for row in self.db.execute('SELECT body FROM evidence WHERE run_id=?', (run['id'],)):
            self._artifact(strict_json(row['body']))

    def discover(self, source_root):
        with self.lock:
            self._integrity()
            if not self.policy['project']['has_ui']:
                return {'phase': 'not_applicable', 'run_id': None, 'source_sha256': None, 'production_visual_verified': False}
            raw = Path(source_root)
            require(not raw.is_symlink(), 'source symlink unsupported')
            root = raw.resolve()
            require(not self.root.is_relative_to(root) or self.root.is_relative_to(root/'.workflow'), 'UIUX ledger must be outside source or in .workflow')
            source, now = source_digest(root), self._now()
            self.db.execute('BEGIN IMMEDIATE')
            try:
                old = self.db.execute('SELECT * FROM runs WHERE source_root=? ORDER BY rowid DESC LIMIT 1', (str(root),)).fetchone()
                if old and old['source_sha256'] == source and now < old['next_due']:
                    self.db.execute('COMMIT')
                    return self._discovery_result('unchanged' if old['phase'] == 'review_required' else 'not_due', old['id'], source)
                ident = 'uiux-' + canonical({'policy': self.policy_sha256, 'source': source, 'root': str(root), 'previous': old['id'] if old else None})[:24]
                self.db.execute('INSERT INTO runs VALUES(?,?,?,?,?,?)', (ident, str(root), source, now, now + self.policy['cadence']['interval_seconds'], 'review_required'))
                self.db.execute('COMMIT')
            except BaseException:
                if self.db.in_transaction: self.db.execute('ROLLBACK')
                raise
            return self._discovery_result('review_required', ident, source)

    def _discovery_result(self, phase, ident, source):
        audit = {'kind': 'uiux_evidence_investigation', 'run_id': ident, 'source_sha256': source, 'policy_sha256': self.policy_sha256,
                 'standards': self.policy['standards'], 'production_visual_verified': False,
                 'acceptance_boundary': 'Gather bounded submitted evidence and independent reasons; no production capture verifier or expanded Core authority.'}
        return {'phase': phase, 'run_id': ident, 'source_sha256': source, 'production_visual_verified': False,
                'audit_proposal': {'external_key': 'uiux:due:' + ident, 'title': 'Investigate UI evidence for ' + self.policy['project']['id'],
                                   'body': encoded(audit).decode(), 'commands': [], 'permissions': {}, 'require_owner_approval': False}}

    def record(self, run_id, stage, evidence):
        with self.lock:
            run = self._run(run_id);self._fresh(run)
            require(stage in {'screenshot', 'interaction', 'difference'} and isinstance(evidence, dict)
                    and set(evidence) == {'id', 'kind', 'source_sha256', 'actor', 'artifact'} and text(evidence['id']) and text(evidence['actor'])
                    and evidence['kind'] in {'fixture', 'submitted'} and evidence['source_sha256'] == run['source_sha256'], 'exact unverified source-bound evidence required')
            a = evidence['artifact']
            require(isinstance(a, dict) and set(a) == {'path', 'sha256', 'viewport_id', 'flow_id'} and text(a['path']) and hash_value(a['sha256']), 'exact artifact identity required')
            viewports = {x['id']: x for x in self.policy['standards']['viewports']};flows = {x['id']: x for x in self.policy['standards']['flows']}
            require(isinstance(a['viewport_id'], str) and a['viewport_id'] in viewports and isinstance(a['flow_id'], str) and a['flow_id'] in flows, 'owner viewport and flow required')
            data = self._artifact(evidence)
            if stage == 'screenshot':
                viewport = viewports[a['viewport_id']]
                validate_png(data, viewport['width'], viewport['height'])
            else:
                body = strict_json(data)
                require(isinstance(body, dict) and len(encoded(body)) <= self.policy['limits']['artifact_bytes'], 'bounded JSON evidence required')
                if stage == 'interaction':
                    require(set(body) == {'flow_id', 'steps'} and body['flow_id'] == a['flow_id'] and isinstance(body['steps'], list)
                            and len(body['steps']) == len(flows[a['flow_id']]['steps']), 'exact owner interaction flow required')
                    for item, step in zip(body['steps'], flows[a['flow_id']]['steps']):
                        require(isinstance(item, dict) and set(item) == {'step', 'selector', 'outcome'} and item['step'] == step
                                and text(item['selector']) and item['outcome'] in {'passed', 'failed'}, 'source-bound interaction steps required')
            value = encoded(evidence).decode()
            self.db.execute('BEGIN IMMEDIATE')
            try:
                self._fresh(run)
                old = self.db.execute('SELECT * FROM evidence WHERE run_id=? AND id=?', (run_id, evidence['id'])).fetchone()
                if old: require(old['stage'] == stage and old['body'] == value, 'evidence ID is immutable')
                else:
                    require(self.db.execute('SELECT COUNT(*) FROM evidence WHERE run_id=?', (run_id,)).fetchone()[0] < self.policy['limits']['evidence_items'], 'evidence item bound exceeded')
                    require(not self.db.execute('SELECT 1 FROM reviews WHERE run_id=?', (run_id,)).fetchone(), 'reviewed evidence is frozen; discover next review')
                    self.db.execute('INSERT INTO evidence VALUES(?,?,?,?)', (run_id, evidence['id'], stage, value))
                self.db.execute('COMMIT')
            except BaseException:
                if self.db.in_transaction: self.db.execute('ROLLBACK')
                raise
            return {'run_id': run_id, 'evidence_id': evidence['id'], 'stage': stage, 'policy_sha256': self.policy_sha256, 'artifact_sha256': a['sha256'], 'production_visual_verified': False}

    def review(self, run_id, reviewer, rubric, verdict, rationale, findings):
        with self.lock:
            run = self._run(run_id);self._fresh(run)
            rows = self.db.execute('SELECT * FROM evidence WHERE run_id=?', (run_id,)).fetchall()
            packets = {x['id']: strict_json(x['body']) for x in rows}
            require(text(reviewer) and all(x['actor'] != reviewer for x in packets.values()), 'independent evidence reviewer required')
            require(verdict in {'needs_improvement', 'acceptable'} and text(rationale), 'reasoned review verdict required')
            require(isinstance(rubric, list) and len(rubric) == len(self.policy['rubric']), 'complete owner rubric required')
            scores = {};owner = {x['id']: x for x in self.policy['rubric']}
            def references(ids):
                return strings(ids, False) and set(ids) <= set(packets)
            for item in rubric:
                require(isinstance(item, dict) and set(item) == {'id', 'score', 'reason', 'evidence_ids'} and isinstance(item['id'], str)
                        and item['id'] in owner and item['id'] not in scores and type(item['score']) is int and 0 <= item['score'] <= 4
                        and text(item['reason']) and references(item['evidence_ids']), 'concrete source-bound rubric reasons required')
                scores[item['id']] = item['score']
            require(verdict != 'acceptable' or all(scores[x] >= owner[x]['minimum_score'] for x in owner), 'acceptable verdict fails owner rubric')
            require(isinstance(findings, list) and len(findings) <= 64, 'bounded actionable findings required')
            ids = set()
            for f in findings:
                require(isinstance(f, dict) and set(f) == {'id', 'title', 'description', 'evidence_ids', 'major_brand_change', 'brand_change_id'}
                        and text(f['id']) and f['id'] not in ids and text(f['title']) and text(f['description']) and references(f['evidence_ids'])
                        and type(f['major_brand_change']) is bool and (text(f['brand_change_id']) if f['major_brand_change'] else f['brand_change_id'] is None), 'exact actionable finding required')
                ids.add(f['id'])
            covered_views = {strict_json(x['body'])['artifact']['viewport_id'] for x in rows if x['stage'] == 'screenshot'}
            covered_flows = {strict_json(x['body'])['artifact']['flow_id'] for x in rows if x['stage'] == 'interaction'}
            complete = covered_views == {x['id'] for x in self.policy['standards']['viewports']} and covered_flows == {x['id'] for x in self.policy['standards']['flows']}
            if not complete: return {'run_id': run_id, 'phase': 'evidence_required', 'proposals': [], 'production_visual_verified': False}
            review = {'reviewer': reviewer, 'rubric': rubric, 'verdict': verdict, 'rationale': rationale, 'findings': findings}
            review_sha = canonical(review)
            self.db.execute('BEGIN IMMEDIATE')
            try:
                self.db.execute('INSERT OR IGNORE INTO reviews VALUES(?,?,?,?)', (run_id, review_sha, encoded(review).decode(), 'unverified'))
                for f in findings:
                    blocked = f['major_brand_change'] and f['brand_change_id'] not in self.policy['approved_brand_changes']
                    key = 'uiux:' + canonical({'policy': self.policy_sha256, 'run': run_id, 'finding': f['id']})
                    body = {'kind': 'uiux_investigation', 'run_id': run_id, 'source_sha256': run['source_sha256'], 'policy_sha256': self.policy_sha256,
                            'finding': f, 'evidence': [{'id': k, 'stage': next(x['stage'] for x in rows if x['id'] == k), 'artifact_sha256': packets[k]['artifact']['sha256']} for k in f['evidence_ids']],
                            'rationale': rationale, 'production_visual_verified': False, 'acceptance_boundary': 'Investigate submitted evidence; fixed Core acceptance and owner permissions still apply.'}
                    proposal = {'external_key': key, 'title': f['title'], 'body': encoded(body).decode(), 'commands': [], 'permissions': {}, 'require_owner_approval': bool(blocked)}
                    old = self.db.execute('SELECT * FROM proposals WHERE external_key=?', (key,)).fetchone()
                    if old: require(old['body'] == encoded(proposal).decode() and old['review_sha'] == review_sha, 'finding export identity is immutable')
                    else: self.db.execute('INSERT INTO proposals VALUES(?,?,?,?,?,0)', (key, run_id, review_sha, encoded(proposal).decode(), int(blocked)))
                self.db.execute('UPDATE runs SET phase=? WHERE id=?', ('unverified', run_id))
                self.db.execute('COMMIT')
            except BaseException:
                if self.db.in_transaction: self.db.execute('ROLLBACK')
                raise
            result = []
            for row in self.db.execute('SELECT * FROM proposals WHERE run_id=? AND review_sha=? ORDER BY external_key', (run_id, review_sha)).fetchall():
                proposal = strict_json(row['body'])
                if not row['blocked'] and not row['exported'] and self.task_sink is not None:
                    self._fresh(run);self.task_sink(copy.deepcopy(proposal))
                    self.db.execute('UPDATE proposals SET exported=1 WHERE external_key=?', (row['external_key'],))
                result.append(dict(proposal, blocked=bool(row['blocked'])))
            return {'run_id': run_id, 'phase': 'unverified', 'review_sha256': review_sha, 'proposals': result, 'production_visual_verified': False}

    def status(self):
        with self.lock:
            self._integrity()
            runs, stale = [], []
            for row in self.db.execute('SELECT * FROM runs ORDER BY rowid'):
                r = dict(row)
                try: self._fresh(r)
                except (ValueError, OSError): stale.append(r['id']);r['phase'] = 'stale'
                r['policy_sha256'] = self.policy_sha256;r['production_visual_verified'] = False
                r['evidence'] = [{'id': x['id'], 'stage': x['stage'], 'packet': strict_json(x['body']), 'production_visual_verified': False} for x in self.db.execute('SELECT * FROM evidence WHERE run_id=? ORDER BY id', (r['id'],))]
                runs.append(r)
            return {'project': self.policy['project'], 'applicability': 'applicable' if self.policy['project']['has_ui'] else 'not_applicable',
                    'policy_sha256': self.policy_sha256, 'runs': runs, 'stale_run_ids': stale, 'production_visual_verified': False}

    def query(self):
        return self.status()
