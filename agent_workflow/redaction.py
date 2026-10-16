"""Bounded public projections, never canonical private evidence or its hashes.

This is defense in depth for JSON-like output, not a proof that arbitrary text
contains no secrets. Callers retain access control and review exported artifacts.
"""
from __future__ import annotations

import base64
from collections.abc import Iterable
import json
import re

POLICY_VERSION = 'public-v1'
_KEYS = frozenset({'token', 'accesstoken', 'refreshtoken', 'authtoken', 'idtoken',
                   'apikey', 'secret', 'secretkey', 'clientsecret', 'password',
                   'passwd', 'authorization', 'cookie', 'setcookie', 'credentials',
                   'xapikey', 'sessiontoken', 'bearertoken', 'securitytoken',
                   'secretaccesskey', 'awssecretaccesskey'})
_FENCE = re.compile(r'(?<![0-9a-fA-F])[0-9a-fA-F]{48}(?![0-9a-fA-F])')
_PROVIDER = re.compile(r'\bsk-(?:proj-)?[A-Za-z0-9_-]{16,}')
_AUTH = re.compile(r'(?i)(\b(?:bearer|basic)\s+)[A-Za-z0-9._~+/=-]+')
_ASSIGN = re.compile(r'''(?ix)(\b(?:api[_-]?key|(?:access|refresh|auth|id|session|bearer|security)[_-]?token|token|password|passwd|client[_-]?secret|secret|authorization|(?:set-)?cookie)\b["']?\s*[:=]\s*["']?)([^\s"',;}\]&]+)''')
_QUOTED_ASSIGN = re.compile(r'''(?ix)(\b(?:api[_-]?key|(?:access|refresh|auth|id|session|bearer|security)[_-]?token|token|password|passwd|client[_-]?secret|secret|authorization|(?:set-)?cookie)\b["']?\s*[:=]\s*)(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')''')
_COOKIE_HEADER = re.compile(r'(?im)(\b(?:set-cookie|cookie)\s*:\s*)[^\r\n]*')
_USERINFO = re.compile(r'(?<=://)[^/\s:@]+:[^/\s@]+@')
_ENCODED = re.compile(r'[A-Za-z0-9_+/=-]{20,8192}')
_ESCAPE = re.compile(r'\\u([0-9a-fA-F]{4})|\\x([0-9a-fA-F]{2})|%([0-9a-fA-F]{2})')


def public(value: object, *, known_secrets: Iterable[str] = (), marker: str = '[REDACTED]') -> object:
    """Return a new projection; 40/64-hex identity/hash fields stay unchanged.

    Recognize credential keys, labelled/provider-shaped text, Core's 48-hex
    fencing tokens and bounded common encodings. Unlabelled unknown opaque
    values cannot be reliably identified. Oversize/deep fields are omitted.
    """
    secrets = tuple(known_secrets)
    if len(secrets) > 128 or any(not isinstance(x, str) or not x or len(x) > 4096 for x in secrets):
        raise ValueError('bounded explicit secret set required')
    return _public(value, secrets, marker, 0)


def _public(value: object, secrets: tuple[str, ...], marker: str, depth: int) -> object:
    if depth > 16:
        return marker
    if isinstance(value, dict):
        return {(_string(k, secrets, marker, depth + 1) if isinstance(k, str) else k):
                marker if isinstance(k, str) and re.sub(r'[^a-z]', '', k.lower()) in _KEYS
                else _public(v, secrets, marker, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return [_public(v, secrets, marker, depth + 1) for v in value]
    if isinstance(value, str):
        return _string(value, secrets, marker, depth)
    return value


def _string(value: str, secrets: tuple[str, ...], marker: str, depth: int) -> str:
    if depth > 16 or len(value) > 131072:
        return marker
    if len(value) in (40, 64) and re.fullmatch(r'[0-9a-fA-F]+', value) and value not in secrets:
        return value  # Preserve identity/hash strings even if their hex decodes as text.
    # Parse original JSON before text patterns: replacing inside an escaped JSON
    # string can invalidate its quoting and expose the rest of a credential.
    try:
        nested = json.loads(value)
    except (ValueError, RecursionError):
        nested = None
    if isinstance(nested, (dict, list)):
        cleaned = _public(nested, secrets, marker, depth + 1)
        return json.dumps(cleaned, ensure_ascii=False) if cleaned != nested else value
    # Embedded program/log syntax can add another quoting layer. Without a
    # complete JSON container, omit such credential-bearing text conservatively.
    if '\\' in value and _ASSIGN.search(value):
        return marker
    text = value
    for secret in secrets:
        text = text.replace(secret, marker)
    text = _COOKIE_HEADER.sub(lambda m: m[1] + marker, text)
    text = _QUOTED_ASSIGN.sub(lambda m: m[1] + marker, text)
    text = _FENCE.sub(lambda _: marker, text)
    text = _PROVIDER.sub(lambda _: marker, text)
    text = _AUTH.sub(lambda m: m[1] + marker, text)
    text = _ASSIGN.sub(lambda m: m[0] if m[2] in {marker, marker[:-1] if marker.endswith(']') else marker} else m[1] + marker, text)
    text = _USERINFO.sub(lambda _: marker + '@', text)
    escaped = _ESCAPE.sub(lambda m: chr(int(next(x for x in m.groups() if x is not None), 16)), text)
    if escaped != text and _string(escaped, secrets, marker, depth + 1) != escaped:
        return marker
    if depth < 16:
        def encoded(match: re.Match[str]) -> str:
            raw = match[0]
            decoded: list[str] = []
            try:
                decoded.append(base64.b64decode(raw + '=' * ((-len(raw)) % 4), altchars=b'-_', validate=True).decode('utf-8'))
            except (ValueError, UnicodeError):
                pass
            if len(raw) % 2 == 0 and re.fullmatch(r'[0-9a-fA-F]+', raw):
                try:
                    decoded.append(bytes.fromhex(raw).decode('utf-8'))
                except (ValueError, UnicodeError):
                    pass
            if any(_string(item, secrets, marker, depth + 1) != item for item in decoded):
                return marker
            return raw
        text = _ENCODED.sub(encoded, text)
    return text
