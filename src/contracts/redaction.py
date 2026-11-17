"""The shared redaction filter for contract messages and log events.

Rules, applied to every `context` or `details` mapping:

- Credentials, tokens, PINs and similar keys: value becomes `[redacted]`.
- Message and audio content (text, transcripts, DTMF digits): value becomes
  `[omitted]`. Content is never logged by default.
- Location: latitude and longitude are rounded to one decimal place (about
  11 km) and grid squares are cut to four characters.
- Strings that look like `Bearer ...` or `user:pass@host` credentials are
  masked wherever they appear.
- Anything that is not plain JSON data is replaced by a placeholder naming
  its type, never by `repr()`, which could leak.

The filter never raises: a bad value is replaced, not propagated. It copies;
it does not modify what it is given.
"""
from __future__ import annotations

import re
from typing import Any, Mapping

REDACTED = "[redacted]"
OMITTED = "[omitted]"
TRUNCATED = "[truncated]"

MAX_DEPTH = 6
MAX_ITEMS = 50
MAX_STRING = 500

_SECRET_WORDS = frozenset({
    "password", "passwd", "passphrase", "passcode", "pin", "secret", "token",
    "authorization", "cookie", "credential", "credentials", "apikey",
})
_SECRET_PAIRS = frozenset({("api", "key"), ("private", "key"), ("access", "key"), ("auth", "key")})
_CONTENT_WORDS = frozenset({"text", "body", "transcript", "content", "audio", "digits", "dtmf", "message"})
_LAT_WORDS = frozenset({"lat", "latitude"})
_LON_WORDS = frozenset({"lon", "lng", "long", "longitude"})
_GRID_WORDS = frozenset({"grid", "gridsquare", "maidenhead"})

_SPLIT = re.compile(r"[^A-Za-z0-9]+|(?<=[a-z0-9])(?=[A-Z])")
_BEARER = re.compile(r"\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]{6,}", re.IGNORECASE)
_URL_USERINFO = re.compile(r"(://)[^/\s:@]+:[^/\s@]+@")


def _words(key: str) -> list[str]:
    return [word.lower() for word in _SPLIT.split(key) if word]


def _is_secret(words: list[str]) -> bool:
    if any(word in _SECRET_WORDS for word in words):
        return True
    return any(pair in _SECRET_PAIRS for pair in zip(words, words[1:]))


def _clean_string(value: str) -> str:
    value = _BEARER.sub(r"\1 " + REDACTED, value)
    value = _URL_USERINFO.sub(r"\1" + REDACTED + "@", value)
    if len(value) > MAX_STRING:
        value = value[:MAX_STRING] + TRUNCATED
    return value


def _coarse(value: Any) -> Any:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return REDACTED
    return round(float(value), 1)


def _value(key: str, value: Any, depth: int) -> Any:
    words = _words(key)
    if _is_secret(words):
        return REDACTED if value not in (None, "", False) else value
    if any(word in _CONTENT_WORDS for word in words):
        return OMITTED if value not in (None, "") else value
    if any(word in _LAT_WORDS | _LON_WORDS for word in words):
        return _coarse(value) if value is not None else None
    if any(word in _GRID_WORDS for word in words):
        return value[:4] if isinstance(value, str) else REDACTED
    return _clean(value, depth)


def _clean(value: Any, depth: int) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _clean_string(value)
    if depth >= MAX_DEPTH:
        return TRUNCATED
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= MAX_ITEMS:
                out[TRUNCATED] = len(value) - MAX_ITEMS
                break
            name = key if isinstance(key, str) else str(type(key).__name__)
            out[name] = _value(name, item, depth + 1)
        return out
    if isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)[:MAX_ITEMS]
        return [_clean(item, depth + 1) for item in items]
    return f"[{type(value).__name__}]"


def redact(context: Mapping[str, Any]) -> dict[str, Any]:
    """Return a redacted, JSON-safe copy of `context`."""
    return _clean(dict(context), 0)


def find_sensitive_keys(values: Mapping[str, Any], _path: str = "", _depth: int = 0) -> list[str]:
    """Paths of keys in `values` that look like credentials.

    For messages that must carry real values but must never carry secrets
    (configuration): reject them rather than silently replace them.
    """
    found: list[str] = []
    if _depth >= MAX_DEPTH:
        return found
    for key, item in values.items():
        name = key if isinstance(key, str) else str(type(key).__name__)
        path = f"{_path}.{name}" if _path else name
        if _is_secret(_words(name)) and item not in (None, "", False):
            found.append(path)
        elif isinstance(item, Mapping):
            found.extend(find_sensitive_keys(item, path, _depth + 1))
        elif isinstance(item, (list, tuple)):
            for index, element in enumerate(item):
                if isinstance(element, Mapping):
                    found.extend(find_sensitive_keys(element, f"{path}[{index}]", _depth + 1))
    return found
