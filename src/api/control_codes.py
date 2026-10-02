"""One-time codes for DTMF macros (RFC 6238 TOTP, as authenticator apps make).

DTMF goes out in the clear, so anyone listening can repeat a control code.
A macro marked "needs a code" is followed by the 6-digit code from the
keyer's authenticator app, which changes every 30 seconds and is accepted
once. Each dashboard user sets up their own secret, so the audit log shows
whose code it was.

The secrets live in their own file (`data/control-codes.json`, readable only
by the service), out of the settings, the API's responses and backups.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets
import struct
import threading
import time
from typing import Callable, Optional
from urllib.parse import quote, urlencode

from .persistence import StateStore

_logger = logging.getLogger("moreopenrepeater.control_codes")

STEP_SECONDS = 30
DIGITS = 6
DRIFT_STEPS = 1  # also accept the previous and next code, for a slow keyer or a phone's clock
MAX_FAILURES = 5
FAILURE_WINDOW_SECONDS = 10 * 60
LOCKOUT_SECONDS = 10 * 60


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def totp(secret: str, step: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10**DIGITS).zfill(DIGITS)


def provisioning_uri(secret: str, username: str, issuer: str) -> str:
    label = quote(f"{issuer}:{username}")
    return f"otpauth://totp/{label}?" + urlencode({"secret": secret, "issuer": issuer, "digits": DIGITS, "period": STEP_SECONDS})


class ControlCodes:
    """`store=None` keeps the secrets in memory (tests)."""

    def __init__(self, store: Optional[StateStore], clock: Callable[[], float] = time.time) -> None:
        self._store = store
        self._clock = clock
        data = (store.load() if store is not None else None) or {}
        self._users: dict[str, dict] = data.get("users", {})
        self._failures: list[float] = []
        self.locked_until: Optional[float] = None
        self._lock = threading.Lock()  # checked from the audio thread, set up from the API

    def _save(self) -> None:
        if self._store is not None:
            self._store.save({"users": self._users})

    def _step(self) -> int:
        return int(self._clock() // STEP_SECONDS)

    # -- setting up ---------------------------------------------------------

    def begin(self, username: str) -> str:
        """A new secret for `username`, used only once confirmed with a code
        from it. Until then any secret they already had keeps working."""
        secret = new_secret()
        with self._lock:
            self._users.setdefault(username, {})["pending"] = secret
            self._save()
        return secret

    def confirm(self, username: str, code: str) -> bool:
        with self._lock:
            secret = self._users.get(username, {}).get("pending")
            if not secret:
                return False
            step = self._matching_step(secret, code, last_step=-1)
            if step is None:
                return False
            self._users[username] = {"secret": secret, "last_step": step, "since": self._clock()}
            self._save()
        _logger.info("%s set up one-time codes", username)
        return True

    def remove(self, username: str) -> None:
        with self._lock:
            if self._users.pop(username, None) is not None:
                self._save()

    def status(self, username: str) -> dict:
        user = self._users.get(username, {})
        return {"enrolled": "secret" in user, "since": user.get("since"), "pending": "pending" in user}

    def enrolled(self) -> list[dict]:
        return [{"username": name, "since": user["since"]} for name, user in sorted(self._users.items()) if "secret" in user]

    # -- checking -----------------------------------------------------------

    def _matching_step(self, secret: str, code: str, last_step: int) -> Optional[int]:
        now = self._step()
        for step in range(now - DRIFT_STEPS, now + DRIFT_STEPS + 1):
            if step > last_step and hmac.compare_digest(totp(secret, step), code):
                return step
        return None

    def locked(self) -> bool:
        if self.locked_until is not None and self._clock() >= self.locked_until:
            self.locked_until = None
        return self.locked_until is not None

    def check(self, code: str) -> Optional[str]:
        """Whose code `code` is, or None. A code is accepted once; too many
        wrong ones in a row lock all codes out for a while."""
        with self._lock:
            if self.locked():
                return None
            for username, user in self._users.items():
                if "secret" not in user:
                    continue
                step = self._matching_step(user["secret"], code, user.get("last_step", -1))
                if step is not None:
                    user["last_step"] = step
                    self._save()
                    self._failures = []
                    return username
            now = self._clock()
            self._failures = [t for t in self._failures if now - t < FAILURE_WINDOW_SECONDS] + [now]
            if len(self._failures) >= MAX_FAILURES:
                self.locked_until = now + LOCKOUT_SECONDS
                self._failures = []
                _logger.warning("%d wrong one-time codes; codes are locked out for %d minutes", MAX_FAILURES, LOCKOUT_SECONDS // 60)
            return None
