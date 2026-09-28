"""Optional auth for the dashboard/API: a login page backed by server-side
sessions, plus HTTP Basic for scripts/curl.

Disabled by default (matches every other env-gated deployment concern in
this package -- `link`/APRS are opt-in the same way), which keeps the
existing tests and the local dev workflow credential-free. Enabling it
requires *both* `MOREOPENREPEATER_AUTH_USER` and `MOREOPENREPEATER_AUTH_PASSWORD`
-- setting only one is almost certainly a typo'd attempt to enable auth, so
it fails loudly at startup rather than silently leaving the API open.

The dashboard signs in through `POST /api/login`, which issues an opaque
random token stored in an HttpOnly cookie. Unlike a Basic `Authorization`
header, browsers *do* attach cookies to a `new WebSocket(...)` handshake, so
`/ws/status` authenticates the same way as every REST route. Sessions live
in process memory: a restart signs everyone out, which is an acceptable
trade for a single-user controller on a Pi and means logout really revokes
the token instead of waiting for a signed cookie to expire.

`fastapi.security.HTTPBasic` isn't used: its dependency is typed against
`Request`, and FastAPI's docs say a dependency shared with `@app.websocket`
routes needs the more general `HTTPConnection` instead.
"""
from __future__ import annotations

import base64
import secrets
import time
from typing import Callable, NamedTuple, Optional

SESSION_COOKIE_NAME = "mor_session"
SESSION_TTL_SECONDS = 12 * 60 * 60


class AuthSettings(NamedTuple):
    username: str
    password: str


def auth_settings_from_env(env: dict) -> Optional[AuthSettings]:
    username = env.get("MOREOPENREPEATER_AUTH_USER")
    password = env.get("MOREOPENREPEATER_AUTH_PASSWORD")
    if not username and not password:
        return None
    if not username or not password:
        raise RuntimeError(
            "Set both MOREOPENREPEATER_AUTH_USER and MOREOPENREPEATER_AUTH_PASSWORD "
            "to enable auth, or neither to disable it -- only one was set."
        )
    return AuthSettings(username=username, password=password)


def parse_basic_auth_header(header_value: Optional[str]) -> Optional[tuple[str, str]]:
    """Decode an `Authorization: Basic <base64>` header into (username,
    password); None if the header is missing or malformed."""
    if not header_value or not header_value.startswith("Basic "):
        return None
    try:
        decoded = base64.b64decode(header_value.removeprefix("Basic ").encode("ascii")).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    username, sep, password = decoded.partition(":")
    if not sep:
        return None
    return username, password


def verify_credentials(username: str, password: str, settings: AuthSettings) -> bool:
    """Constant-time comparison -- a naive `==` leaks timing information
    about how many leading characters matched, letting an attacker recover
    the password/username one character at a time. Both comparisons always
    run so a wrong username isn't distinguishable from a wrong password."""
    username_ok = secrets.compare_digest(username.encode(), settings.username.encode())
    password_ok = secrets.compare_digest(password.encode(), settings.password.encode())
    return username_ok and password_ok


def credentials_match(header_value: Optional[str], settings: AuthSettings) -> bool:
    parsed = parse_basic_auth_header(header_value)
    if parsed is None:
        return False
    return verify_credentials(*parsed, settings)


class SessionStore:
    """In-memory map of opaque session tokens to (username, expiry)."""

    def __init__(self, ttl_seconds: float = SESSION_TTL_SECONDS, clock: Callable[[], float] = time.monotonic) -> None:
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._sessions: dict[str, tuple[str, float]] = {}

    def create(self, username: str) -> str:
        self._purge_expired()
        token = secrets.token_urlsafe(32)
        self._sessions[token] = (username, self._clock() + self._ttl_seconds)
        return token

    def username_for(self, token: Optional[str]) -> Optional[str]:
        if not token:
            return None
        entry = self._sessions.get(token)
        if entry is None:
            return None
        username, expires_at = entry
        if self._clock() >= expires_at:
            del self._sessions[token]
            return None
        return username

    def revoke(self, token: Optional[str]) -> None:
        if token:
            self._sessions.pop(token, None)

    def _purge_expired(self) -> None:
        now = self._clock()
        for token in [t for t, (_, expires_at) in self._sessions.items() if now >= expires_at]:
            del self._sessions[token]
