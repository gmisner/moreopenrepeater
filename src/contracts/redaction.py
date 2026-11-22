"""The shared redaction filter for contract messages and log events.

Rules, applied to every `context` or `details` mapping:

- Credentials, tokens, PINs and similar keys: value becomes `[redacted]`.
- Message and audio content (text, transcripts, DTMF digits): value becomes
  `[omitted]`. Content is never logged by default.
- Location: latitude and longitude are rounded to one decimal place (about
  11 km) and grid squares are cut to four characters.
- Strings that look like `Bearer ...` or `user:pass@host` credentials, or
  like `password=x`, `token: x` or `"key": "x"` (a secret word, then `=` or
  `:`, then a value), are masked wherever they appear, in free text too.
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

# Keys are split into words (`rx_gpio_pin`, `rxGpioPin` and `rx-gpio-pin` are all rx, gpio, pin) and a key is
# a secret when one of its words is a secret word. Whole words only: `mapping` and `spinner` stay readable.
_SECRET_WORDS = frozenset({
    "password", "passwd", "pwd", "pass", "passphrase", "passcode", "pin", "psk", "secret", "token",
    "authorization", "cookie", "credential", "credentials", "apikey",
})
_SECRET_PAIRS = frozenset({("api", "key"), ("private", "key"), ("access", "key"), ("auth", "key")})
#: A bare `key` or `session` is only a secret when it is what the key is *about* (`key`, `ssh_key`,
#: `session`, `session_id`), not a modifier (`session_count`, `key_up_ms`).
_SECRET_WHEN_LAST = frozenset({"key", "session"})
_SESSION_FOLLOWERS = frozenset({"id", "key", "token", "cookie", "secret", "data"})
#: A pin number of a piece of hardware is not a PIN code.
_HARDWARE_WORDS = frozenset({"gpio", "ptt", "cos", "cm108", "fan", "led", "relay", "header"})
_CONTENT_WORDS = frozenset({"text", "body", "transcript", "content", "audio", "digits", "dtmf", "message"})
#: A content word followed by one of these describes the content (`audio_device`, `content_type`,
#: `text_length`, `dtmf_mute`) instead of being it, so its value is kept. Anything else after it, or
#: nothing, leaves the key a content key: when unsure the value is omitted.
_CONTENT_PROPERTY_WORDS = frozenset({
    "device", "devices", "rate", "level", "levels", "type", "length", "len", "count", "size", "enabled", "mute",
    "muted", "hold", "threshold", "gain", "format", "codec", "channels", "card", "id", "index", "input", "output",
    "route", "source", "state", "status", "mode", "ms", "seconds", "timeout", "volume", "name",
})
#: `error_message` is an error's wording, written by code; a bare `message` or `sms_message` is a person's.
_MESSAGE_QUALIFIERS = frozenset({
    "error", "status", "log", "warning", "failure", "reason", "exit", "result", "debug", "info", "notice",
    "exception",
})
_LAT_WORDS = frozenset({"lat", "latitude"})
_LON_WORDS = frozenset({"lon", "lng", "long", "longitude"})
_GRID_WORDS = frozenset({"grid", "gridsquare", "maidenhead"})

_SPLIT = re.compile(r"[^A-Za-z0-9]+|(?<=[a-z0-9])(?=[A-Z])")
_BEARER = re.compile(r"\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]{6,}", re.IGNORECASE)
_URL_USERINFO = re.compile(r"(://)[^/\s:@]+:[^/\s@]+@")
# The lookbehind keeps a long run of word characters from being tried again from every position in it (that was
# quadratic: 45 ms for 2000 characters, and this runs on the thread that logs).
# `name=value`, `name: value`, `"name": "value"`, `'name': 'value'`: the name is judged by the same rules as a
# context key, so `password=x` and `X-Auth-Token: x` are masked and `rx_gpio_pin=17` is not.
_KEY_START = re.compile(r"""(?<![A-Za-z0-9_.-])(?P<name>[A-Za-z0-9_.-]+)(?P<sep>["']?\s*[=:]\s*)""")
_VALUE = re.compile(r"""\"[^"]*\"|'[^']*'|\[redacted\]|[^\s,;&}\]"']+""")


def _words(key: str) -> list[str]:
    return [word.lower() for word in _SPLIT.split(key) if word]


def _is_secret(words: list[str]) -> bool:
    for index, word in enumerate(words):
        if word == "pin":
            if not _HARDWARE_WORDS.intersection(words):
                return True
        elif word in _SECRET_WORDS:
            return True
        elif word in _SECRET_WHEN_LAST and (
            index == len(words) - 1
            or (word == "session" and words[index + 1] in _SESSION_FOLLOWERS)
        ):
            return True
    return any(pair in _SECRET_PAIRS for pair in zip(words, words[1:]))


def _is_content(words: list[str]) -> bool:
    for index, word in enumerate(words):
        if word not in _CONTENT_WORDS:
            continue
        following = words[index + 1] if index + 1 < len(words) else None
        if following in _CONTENT_PROPERTY_WORDS:
            continue
        if word == "message" and following is None and index > 0 and set(words[:index]) <= _MESSAGE_QUALIFIERS:
            continue
        return True
    return False


def mask_text(value: str) -> str:
    """`value` with credentials masked, for free text (a log line, a message): `Bearer` and `Basic`
    headers, `user:pass@host`, and `secret-word=value` or `secret-word: value` pairs. Never raises."""
    try:
        value = _BEARER.sub(r"\1 " + REDACTED, value)
        value = _URL_USERINFO.sub(r"\1" + REDACTED + "@", value)
        out: list[str] = []
        position = 0
        while True:
            match = _KEY_START.search(value, position)
            if match is None:
                out.append(value[position:])
                return "".join(out)
            out.append(value[position:match.end()])
            position = match.end()
            # Only a secret's value is looked at, so the scan stays linear. A name that is not a secret is
            # passed over and the search goes on from its value, which may itself start a pair
            # (`failed: token=abc` is a `failed` pair whose value starts a `token` pair).
            if _is_secret(_words(match.group("name"))):
                secret = _VALUE.match(value, position)
                if secret is not None and secret.group(0) != REDACTED:
                    text = secret.group(0)
                    quote = text[0] if text[0] in "\"'" else ""
                    out.append(quote + REDACTED + quote)
                    position = secret.end()
    except Exception:  # noqa: BLE001 - a line that cannot be checked is dropped, not passed on
        return TRUNCATED


def _clean_string(value: str) -> str:
    value = mask_text(value)
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
    if _is_content(words):
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
