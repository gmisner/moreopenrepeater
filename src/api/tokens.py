"""API tokens: named, revocable credentials for scripts and agents.

A token belongs to a user and acts with that user's current role, so a role
change or deleting the user applies to its tokens immediately. Only a
SHA-256 digest is stored: tokens are 256 random bits, so a slow hash like
the passwords' scrypt would add nothing but per-request cost. The token
itself is shown once, when it's created.
"""
from __future__ import annotations

import hashlib
import secrets
import threading
import time
from dataclasses import dataclass, replace
from typing import Callable, Optional

from .persistence import StateStore

PREFIX = "mor_"
# Last-used times are kept in memory and written at most this often, so a
# busy agent doesn't rewrite the file on every request.
_LAST_USED_SAVE_SECONDS = 300.0


@dataclass(frozen=True)
class ApiToken:
    id: str
    name: str
    username: str
    created_at: float
    digest: str
    last_used_at: Optional[float] = None


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class TokenStore:
    """`store=None` keeps tokens in memory (tests)."""

    def __init__(self, store: Optional[StateStore] = None, clock: Callable[[], float] = time.time) -> None:
        self._store = store
        self._clock = clock
        self._lock = threading.Lock()
        self._saved_at = 0.0
        data = store.load() if store else None
        self._tokens: dict[str, ApiToken] = {}
        for entry in (data or {}).get("tokens", []):
            try:
                token = ApiToken(**entry)
            except TypeError:
                continue
            self._tokens[token.id] = token

    def list(self) -> list[ApiToken]:
        return sorted(self._tokens.values(), key=lambda t: t.created_at)

    def create(self, name: str, username: str) -> tuple[ApiToken, str]:
        """The new token's record, and the token itself (not stored anywhere)."""
        with self._lock:
            token_id = secrets.token_hex(4)
            while token_id in self._tokens:
                token_id = secrets.token_hex(4)
            secret = f"{PREFIX}{token_id}_{secrets.token_urlsafe(32)}"
            record = ApiToken(token_id, name.strip(), username, self._clock(), _digest(secret))
            self._tokens[token_id] = record
            self._save()
        return record, secret

    def verify(self, secret: str) -> Optional[ApiToken]:
        if not secret.startswith(PREFIX):
            return None
        token_id, _, rest = secret[len(PREFIX) :].partition("_")
        record = self._tokens.get(token_id)
        if record is None or not rest or not secrets.compare_digest(_digest(secret), record.digest):
            return None
        now = self._clock()
        with self._lock:
            if token_id in self._tokens:
                record = replace(self._tokens[token_id], last_used_at=now)
                self._tokens[token_id] = record
                if now - self._saved_at >= _LAST_USED_SAVE_SECONDS:
                    self._save()
        return record

    def revoke(self, token_id: str) -> ApiToken:
        with self._lock:
            record = self._tokens.pop(token_id)
            self._save()
        return record

    def remove_user(self, username: str) -> None:
        with self._lock:
            kept = {k: t for k, t in self._tokens.items() if t.username != username}
            if len(kept) != len(self._tokens):
                self._tokens = kept
                self._save()

    def _save(self) -> None:
        self._saved_at = self._clock()
        if self._store is not None:
            self._store.save({"tokens": [t.__dict__ for t in self.list()]})
