"""One-time codes for DTMF macros, checked by the core (Stage 3 slice 7c-6c, #229).

A macro that needs a code (`tx_disable` is the obvious one) is the control you want when the
dashboard is down, so the core checks the code itself and never asks the API. The rules are
the ones `api.control_codes` has always had: RFC 6238 TOTP, a code is accepted once, a step
either side is allowed for a slow keyer, and five wrong codes in ten minutes lock all codes
out for ten minutes.

Two processes now touch codes, so each file has one writer:

- `control-codes.json` (secrets and who is enrolled) is written only by the API, which
  enrolls and removes users. The core reads it and never writes it. It re-reads the file
  when it changes, so an enrollment made while the core runs is picked up.
- `control-codes-replay.json` (the last step each user's code was accepted at) is written
  only by the core, so a code cannot be replayed after the core restarts.

It fails closed. A missing or unreadable secrets file means no code is accepted. It imports
no web framework.
"""
from __future__ import annotations

import hmac
import json
import logging
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from api.control_codes import (
    DRIFT_STEPS,
    FAILURE_WINDOW_SECONDS,
    LOCKOUT_SECONDS,
    MAX_FAILURES,
    STEP_SECONDS,
    totp,
)
from api.persistence import StateStore
from observability import log_event

_logger = logging.getLogger("moreopenrepeater.core.codes")


class CoreCodeChecker:
    def __init__(self, secrets_path: Path, replay_path: Path, clock: Callable[[], float] = time.time) -> None:
        self._secrets_path = secrets_path
        self._replay = StateStore(replay_path, mode=0o600)
        self._clock = clock
        self._lock = threading.Lock()  # checked from the audio thread
        self._users: dict[str, dict] = {}
        self._seen: Optional[tuple[int, int]] = None  # (mtime_ns, size) of the secrets file as last read
        self._replayed: dict[str, int] = self._load_replayed()
        self._failures: list[float] = []
        self.locked_until: Optional[float] = None

    def _load_replayed(self) -> dict[str, int]:
        """The last accepted step of each user. A record that is not what this class wrote is ignored with a
        warning, not a crash: the core must start. The cost is that a code accepted in the last minute or so
        could be accepted once more."""
        try:
            data = self._replay.load()
            steps = data.get("last_step") if isinstance(data, dict) else None
            if steps is None:
                return {}
            return {str(name): int(step) for name, step in steps.items()}
        except (AttributeError, TypeError, ValueError):
            _logger.warning("the one-time code replay record is not in the expected form; starting it empty")
            return {}

    @staticmethod
    def _usable(user: object) -> bool:
        """An enrolled user whose secret is a real base32 key and whose last step is a number. One bad entry
        must not make `check` raise for everyone (it runs on the audio thread), so it is left out instead."""
        if not isinstance(user, dict) or not user.get("secret"):
            return False
        try:
            totp(str(user["secret"]), 0)
            int(user.get("last_step", -1))
        except (ValueError, TypeError):  # binascii.Error is a ValueError
            return False
        return True

    # -- the secrets file (read only) ----------------------------------------------------------

    def _reload_if_changed(self) -> None:
        try:
            stat = self._secrets_path.stat()
        except OSError:
            if self._seen != (-1, -1):
                _logger.warning("no one-time code secrets at %s; every code will be refused", self._secrets_path)
            self._users, self._seen = {}, (-1, -1)
            return
        signature = (stat.st_mtime_ns, stat.st_size)
        if signature == self._seen:
            return
        try:
            data = json.loads(self._secrets_path.read_text())
            users = data.get("users", {})
            if not isinstance(users, dict):
                raise ValueError("users is not an object")
        except (OSError, ValueError, AttributeError):
            _logger.warning("the one-time code secrets are unreadable; every code will be refused")
            self._users, self._seen = {}, signature
            return
        self._users = {name: user for name, user in users.items() if self._usable(user)}
        skipped = sum(1 for user in users.values() if isinstance(user, dict) and user.get("secret")) - len(self._users)
        if skipped:
            _logger.warning("%d one-time code secret(s) are not valid and are ignored", skipped)
        self._seen = signature

    # -- checking ------------------------------------------------------------------------------

    def locked(self) -> bool:
        if self.locked_until is not None and self._clock() >= self.locked_until:
            self.locked_until = None
        return self.locked_until is not None

    def _matching_step(self, secret: str, code: str, last_step: int) -> Optional[int]:
        now = int(self._clock() // STEP_SECONDS)
        for step in range(now - DRIFT_STEPS, now + DRIFT_STEPS + 1):
            if step > last_step and hmac.compare_digest(totp(secret, step), code):
                return step
        return None

    def check(self, code: str) -> Optional[str]:
        """Whose code `code` is, or None. A code is accepted once; too many wrong ones lock all
        codes out for a while."""
        with self._lock:
            if self.locked():
                return None
            self._reload_if_changed()
            for username, user in self._users.items():
                last = max(int(user.get("last_step", -1)), self._replayed.get(username, -1))
                step = self._matching_step(str(user["secret"]), code, last)
                if step is not None:
                    self._replayed[username] = step
                    self._remember()
                    self._failures = []
                    return username
            now = self._clock()
            self._failures = [t for t in self._failures if now - t < FAILURE_WINDOW_SECONDS] + [now]
            if len(self._failures) >= MAX_FAILURES:
                self.locked_until = now + LOCKOUT_SECONDS
                self._failures = []
                log_event(
                    _logger, logging.WARNING, "codes.locked_out",
                    "%d wrong one-time codes; codes are locked out for %d minutes" % (MAX_FAILURES, LOCKOUT_SECONDS // 60),
                    error_code="codes.locked_out", failures=MAX_FAILURES, lockout_min=LOCKOUT_SECONDS // 60, facts_in_text=False,
                )
            return None

    def _remember(self) -> None:
        try:
            self._replay.save({"last_step": self._replayed})
        except OSError as error:
            # The code was good and is accepted. Without the record, a restart could let it be
            # used again inside its 30-second step, so say so.
            _logger.warning("could not record the accepted code: %s", type(error).__name__)


def paths(data_root: Path) -> tuple[Path, Path]:
    """Where the secrets (API-written) and the replay record (core-written) live."""
    return data_root / "control-codes.json", data_root / "control-codes-replay.json"
