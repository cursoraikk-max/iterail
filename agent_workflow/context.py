"""Deterministic, bounded source context. Returned text has no authority.

Identity streams every in-repository source byte, including sensitive files, to
match Core. Only approved complete text is exposed. Packet validation checks
integrity; filesystem containment and no-follow opens belong to retrieval.
"""
import ast
import contextlib
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat

MAX_BYTES = 65536
DEFAULT_BYTES = 16384
RUNTIME = {'.git', '.workflow', '__pycache__'}
REASONS = {'binary', 'sensitive', 'budget', 'oversize', 'irrelevant', 'missing', 'unreadable'}
class ContextBudgetError(ValueError):
    """Complete mandatory context cannot fit its effective budget."""


SECRET = re.compile(r'sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|\bBearer\s+[A-Za-z0-9._-]{20,}|\b(?:api[_-]?key|access[_-]?token|password|secret|token)\b\s*[:=]\s*[\"\']?[A-Za-z0-9_/-]{20,}', re.I)


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')


def used_bytes(bundle):
    return len(encoded({k: v for k, v in bundle.items() if k != 'used_bytes'}))


def bound(value, label):
    if type(value) is not int or not 1 <= value <= MAX_BYTES:
        raise ValueError(label + ' must be an integer in 1..65536')
    return value


def identity(value):
    if not isinstance(value, str) or re.fullmatch('[0-9a-f]{64}', value) is None:
        raise ValueError('invalid source digest')
    return value


def path_name(value):
    if (not isinstance(value, str) or not value or '\\' in value or '\x00' in value
            or PurePosixPath(value).is_absolute() or any(p in ('', '.', '..') for p in value.split('/'))
            or PurePosixPath(value).as_posix() != value):
        raise ValueError('normalized relative source path required')
    return value


def required_paths(values):
    if not isinstance(values, (list, tuple)):
        raise TypeError('mandatory_paths must be a path list')
    paths = [path_name(p) for p in values]
    if len(set(paths)) != len(paths):
        raise ValueError('duplicate mandatory path')
    return paths


def sensitive_path(path):
    parts = PurePosixPath(path).parts
    return (any(p in RUNTIME | {'.ssh', '.aws', '.gnupg', '.codex'} or p.startswith('.env') for p in parts)
            or any(p.casefold() in {'credentials', 'credentials.json', 'id_rsa', 'id_ed25519'} for p in parts)
            or PurePosixPath(path).suffix.casefold() in {'.pem', '.key', '.p12', '.pfx'})


def sensitive_value(value, known_secrets=()):
    """Detect established secret forms without changing returned source bytes."""
    if isinstance(value, dict):
        return any(sensitive_value(k, known_secrets) or sensitive_value(v, known_secrets)
                   for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return any(sensitive_value(v, known_secrets) for v in value)
    if not isinstance(value, str):
        return False
    if SECRET.search(value):
        return True
    if known_secrets:
        from .handoff import scrub
        return scrub(value, list(known_secrets)) != value
    return False


def root_path(root):
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError('source root must be a real directory')
    return root.resolve()


@contextlib.contextmanager
def source_file(root, path):
    """Open every relative component without following symlinks."""
    if not hasattr(os, 'O_NOFOLLOW') or not hasattr(os, 'O_DIRECTORY'):
        raise OSError('no-follow source reads unavailable')
    flags = os.O_RDONLY | os.O_NOFOLLOW
    directory = os.open(root, flags | os.O_DIRECTORY)
    fd = None
    try:
        for part in PurePosixPath(path_name(path)).parts[:-1]:
            child = os.open(part, flags | os.O_DIRECTORY, dir_fd=directory)
            os.close(directory)
            directory = child
        fd = os.open(PurePosixPath(path).name, flags, dir_fd=directory)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError('regular source file required')
        with os.fdopen(fd, 'rb') as stream:
            fd = None
            yield stream
    finally:
        if fd is not None:
            os.close(fd)
        os.close(directory)


def source_paths(root):
    paths = []
    for p in sorted(root.rglob('*')):
        rel = p.relative_to(root)
        if any(x in RUNTIME for x in rel.parts) or p.suffix == '.pyc':
            continue
        if p.is_symlink():
            raise ValueError('source symlinks are unsupported')
        if p.is_file():
            paths.append(path_name(rel.as_posix()))
    return paths


def source_identity(root):
    """Streaming equivalent of Core.digest; runtime exclusions are identical."""
    root = root_path(root)
    h = hashlib.sha256()
    for path in source_paths(root):
        h.update(path.encode('utf-8') + b'\0')
        with source_file(root, path) as stream:
            while chunk := stream.read(65536):
                h.update(chunk)
        h.update(b'\0')
    return h.hexdigest()


def read_text(root, path, limit):
    with source_file(root, path) as stream:
        if os.fstat(stream.fileno()).st_size > limit:
            return None, 'oversize'
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        return None, 'oversize'
    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError:
        return None, 'binary'
    if any(ord(c) < 32 and c not in '\n\r\t' for c in text):
        return None, 'binary'
    if SECRET.search(text):
        return None, 'sensitive'
    return text, None


def imports(path, content):
    if not path.endswith('.py'):
        return set()
    try:
        tree = ast.parse(content)
    except (SyntaxError, ValueError, RecursionError):
        return set()
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name.replace('.', '/') for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = str(PurePosixPath(path).parent)
            if node.level:
                parts = [] if base == '.' else base.split('/')
                parts = parts[:max(0, len(parts) - node.level + 1)]
                module = '/'.join(parts + ([node.module.replace('.', '/')] if node.module else []))
            else:
                module = (node.module or '').replace('.', '/')
            modules.add(module)
            modules.update('/'.join(p for p in (module, alias.name) if p) for alias in node.names)
    return {candidate for module in modules if module for candidate in (module + '.py', module + '/__init__.py')}


def retrieve_context(root, query, *, mandatory_paths, byte_budget=DEFAULT_BYTES,
                     max_file_bytes=DEFAULT_BYTES, source_digest, _known_secrets=()):
    budget = bound(byte_budget, 'byte_budget')
    optional_cap = bound(max_file_bytes, 'max_file_bytes')
    expected = identity(source_digest)
    mandatory = required_paths(mandatory_paths)
    if (not isinstance(_known_secrets, (list, tuple))
            or any(not isinstance(v, str) or not v for v in _known_secrets)):
        raise ValueError('known secret context must be a string list')
    if sensitive_value(mandatory, _known_secrets):
        raise ValueError('sensitive mandatory path cannot be exposed')
    if not isinstance(query, str) or len(query.encode('utf-8')) > 2048:
        raise ValueError('query must be UTF8 text of at most 2048 bytes')
    root = root_path(root)
    if source_identity(root) != expected:
        raise ValueError('source digest mismatch before retrieval')
    paths = source_paths(root)
    if sensitive_value(paths, _known_secrets):
        raise ValueError('sensitive source path cannot be exposed')
    for path in mandatory:
        if path not in paths or sensitive_path(path):
            raise ValueError('missing or sensitive mandatory path: ' + path)
    entries = {}
    omitted = {}
    for path in paths:
        if sensitive_path(path):
            reason = 'sensitive'
            text = None
        else:
            try:
                text, reason = read_text(root, path, budget if path in mandatory else optional_cap)
            except OSError:
                text, reason = None, 'unreadable'
        if text is not None and sensitive_value(text, _known_secrets):
            text, reason = None, 'sensitive'
        if reason:
            if path in mandatory:
                error = ContextBudgetError if reason == 'oversize' else ValueError
                raise error('mandatory file cannot be included completely: ' + path + ' (' + reason + ')')
            omitted[path] = reason
        else:
            entries[path] = {'path': path, 'content': text,
                             'sha256': hashlib.sha256(text.encode('utf-8')).hexdigest(),
                             'bytes': len(text.encode('utf-8')), 'mandatory': path in mandatory}
    terms = set(re.findall(r'\w+', query.casefold()))
    scores = {p: sum(min(20, e['content'].casefold().count(t)) + (5 if t in p.casefold() else 0)
                     for t in terms) for p, e in entries.items()}
    seeds = {p for p in entries if p in mandatory or scores[p] > 0}
    for path in sorted(seeds):
        for related in imports(path, entries[path]['content']):
            if related in scores:
                scores[related] += 10
    for path in entries:
        if path not in mandatory:
            omitted[path] = 'budget' if scores[path] > 0 else 'irrelevant'
    bundle = {'schema_version': 1, 'source_digest': expected,
              'files': [entries[p] for p in mandatory],
              'omitted': [{'path': p, 'reason': omitted[p]} for p in sorted(omitted)]}
    if used_bytes(bundle) > budget:
        raise ContextBudgetError('mandatory files and omission metadata exceed byte budget')
    for path in sorted((p for p in entries if p not in mandatory and scores[p] > 0),
                       key=lambda p: (-scores[p], p)):
        proposed = dict(bundle, files=bundle['files'] + [entries[path]],
                        omitted=[x for x in bundle['omitted'] if x['path'] != path])
        if used_bytes(proposed) <= budget:
            bundle = proposed
    bundle['used_bytes'] = used_bytes(bundle)
    validate_context(bundle, expected, mandatory, budget)
    if source_identity(root) != expected:
        raise ValueError('source digest mismatch after retrieval')
    return bundle


def validate_context(bundle, expected_source, mandatory_paths, budget):
    """Validate packet content/hash/membership, not the unseen filesystem."""
    bound(budget, 'budget')
    expected = identity(expected_source)
    mandatory = set(required_paths(mandatory_paths))
    if (not isinstance(bundle, dict) or set(bundle) != {'schema_version', 'source_digest', 'files', 'omitted', 'used_bytes'}
            or type(bundle['schema_version']) is not int or bundle['schema_version'] != 1
            or bundle['source_digest'] != expected or not isinstance(bundle['files'], list)
            or not isinstance(bundle['omitted'], list)):
        raise ValueError('invalid context packet identity')
    seen = set()
    required = set()
    for entry in bundle['files']:
        if not isinstance(entry, dict) or set(entry) != {'path', 'content', 'sha256', 'bytes', 'mandatory'}:
            raise ValueError('invalid file metadata')
        path = path_name(entry['path'])
        text = entry['content']
        if path in seen or sensitive_path(path) or not isinstance(text, str) or SECRET.search(text):
            raise ValueError('duplicate or sensitive file')
        raw = text.encode('utf-8')
        if (any(ord(c) < 32 and c not in '\n\r\t' for c in text)
                or identity(entry['sha256']) != hashlib.sha256(raw).hexdigest()
                or type(entry['bytes']) is not int or entry['bytes'] != len(raw)
                or type(entry['mandatory']) is not bool or entry['mandatory'] != (path in mandatory)):
            raise ValueError('file content or mandatory metadata mismatch')
        seen.add(path)
        if entry['mandatory']:
            required.add(path)
    for entry in bundle['omitted']:
        if not isinstance(entry, dict) or set(entry) != {'path', 'reason'}:
            raise ValueError('invalid omission metadata')
        path = path_name(entry['path'])
        if path in seen or path in mandatory or not isinstance(entry['reason'], str) or entry['reason'] not in REASONS:
            raise ValueError('duplicate, mandatory or invalid omission')
        seen.add(path)
    actual = used_bytes(bundle)
    if (required != mandatory or type(bundle['used_bytes']) is not int
            or bundle['used_bytes'] != actual or actual > budget):
        raise ValueError('mandatory membership or byte accounting mismatch')
    return True


def query_context(engine, query, mandatory_paths='[]', byte_budget='16384', max_file_bytes='16384', if_none_match=''):
    """Thin read-only adapter; the caller cannot choose a root or policy."""
    from .core import WorkflowError
    try:
        def decimal(value):
            if not isinstance(value, str) or not value.isascii() or not value.isdecimal():
                raise ValueError('context bounds require decimal strings')
            return bound(int(value), 'context bound')
        if not isinstance(if_none_match, str) or len(if_none_match.encode('utf-8')) > 2048:
            raise ValueError('if_none_match must be UTF8 text of at most 2048 bytes')
        budget, cap = decimal(byte_budget), decimal(max_file_bytes)
        if not isinstance(mandatory_paths, str):
            raise TypeError('mandatory_paths requires a JSON path list string')
        extras = required_paths(json.loads(mandatory_paths))
        fixed = 'skills/agent-workflow/SKILL.md'
        paths = [fixed] + [p for p in extras if p != fixed]
        frozen = json.loads(json.dumps({k: engine.config[k] for k in ('permissions', 'budget', 'acceptance', 'tests')}))
        from .handoff import tokens
        known_secrets = tokens(engine)
        if sensitive_value(frozen, known_secrets) or sensitive_value(paths, known_secrets):
            raise ValueError('sensitive mandatory rules cannot be exposed')
        result = {'schema_version': 1, 'authority': 'inert_context_only',
                  'mandatory': dict(frozen, paths=paths), 'context': None,
                  'revision': '0'*64, 'not_modified': False}
        # Reserve exact outer metadata plus inner used_bytes key/value overhead.
        reserve = used_bytes(result) - len(encoded(None)) + 32
        inner_budget = budget - reserve
        if inner_budget < 1:
            raise ContextBudgetError(
                f'frozen metadata reservation {reserve} bytes exceeds byte_budget {budget}; '
                f'residual mandatory context budget {inner_budget} bytes. '
                'Retry with a larger byte_budget, at most 65536; '
                'if complete mandatory content still cannot fit, the request cannot be served.')
        root = engine.config['repo']
        try:
            result['context'] = retrieve_context(root, query, mandatory_paths=paths,
                byte_budget=inner_budget, max_file_bytes=cap, source_digest=source_identity(root),
                _known_secrets=known_secrets)
        except ContextBudgetError as error:
            raise ContextBudgetError(
                f'{error}; byte_budget {budget} bytes reserves {reserve} bytes for frozen '
                f'metadata and packet overhead, leaving residual mandatory context budget '
                f'{inner_budget} bytes. Retry with a larger byte_budget, at most 65536; '
                'max_file_bytes caps optional files only. If complete mandatory content '
                'still cannot fit, the request cannot be served.') from error
        if sensitive_value(result, known_secrets):
            raise ValueError('sensitive context cannot be exposed')
        # Bind normalized effective inputs and the complete validated payload.
        # External policy descriptors are inputs, not policy freshness evidence.
        result['revision'] = hashlib.sha256(encoded({
            'config': engine.config, 'workflow': engine.workflow, 'query': query,
            'paths': paths, 'byte_budget': budget, 'max_file_bytes': cap,
            'packet': {k: v for k, v in result.items() if k != 'revision'}
        })).hexdigest()
        result['used_bytes'] = used_bytes(result)
        if result['used_bytes'] > budget:
            raise ValueError('outer context metadata exceeds byte budget')
        if if_none_match == result['revision']:
            compact = {'schema_version': 1, 'authority': 'inert_context_only',
                       'source_digest': result['context']['source_digest'],
                       'revision': result['revision'], 'not_modified': True}
            compact['used_bytes'] = used_bytes(compact)
            if compact['used_bytes'] > budget:
                raise ValueError('conditional context metadata exceeds byte budget')
            return compact
        return result
    except (ValueError, TypeError, OSError) as error:
        raise WorkflowError(str(error)) from error
