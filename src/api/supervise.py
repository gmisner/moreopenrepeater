"""Keep long-running loops alive through an unexpected exception.

An exception that escapes a `while True` loop ends its task for good. For the
loops that carry timers and keying (the controller tick, announcements, the
link radio, the AllStar and DMR audio senders) that silently stops time-outs,
IDs or the lost-unkey guard while PTT stays as it was.

`LoopGuard` is the shared answer: the loop catches the error, tells the guard,
and carries on with the next pass. Logging is rate limited so a fault that
repeats every 50 ms does not flood the log or the SD card.
"""
from __future__ import annotations

import logging
import time
from typing import Callable

LOG_EVERY_SECONDS = 10.0


class LoopGuard:
    def __init__(
        self,
        name: str,
        logger: logging.Logger,
        *,
        clock: Callable[[], float] = time.monotonic,
        log_every: float = LOG_EVERY_SECONDS,
    ) -> None:
        self.name = name
        self.failures = 0  # every failure since start
        self._logger = logger
        self._clock = clock
        self._log_every = log_every
        self._run = 0  # consecutive failures
        self._unlogged = 0
        self._last_logged = float("-inf")

    @property
    def failing(self) -> bool:
        return self._run > 0

    def failed(self, error: BaseException) -> None:
        """Record a failed pass. The first is logged with its traceback at once;
        later ones at most every `log_every` seconds, with a count."""
        self.failures += 1
        self._run += 1
        self._unlogged += 1
        now = self._clock()
        if now - self._last_logged >= self._log_every:
            self._logger.error(
                "%s failed (%d since the last report, %d in a row); carrying on",
                self.name, self._unlogged, self._run, exc_info=error,
            )
            self._last_logged = now
            self._unlogged = 0

    def ok(self) -> None:
        """Record a good pass. Logs once when a failing loop recovers."""
        if self._run:
            self._logger.warning("%s recovered after %d failed passes in a row", self.name, self._run)
            self._run = 0
            self._unlogged = 0
