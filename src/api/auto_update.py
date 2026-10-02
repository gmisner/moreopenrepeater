"""Automatic updates in a quiet window.

When turned on, the repeater checks its release channel during the chosen
hours and days and, if there's a newer version and nobody has used the
repeater for a while (no carrier, linked station or call, and no net), asks
the updater for it exactly as the Updates page's button does. The updater
rolls back a version that doesn't start; that version isn't tried again, so
a bad release isn't retried every night. GitHub allows 60 unauthenticated
API calls an hour, so it checks at most every CHECK_EVERY_SECONDS.

Results reach the alerts like any other update (api.health).
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, time as day_time, timedelta
from typing import Callable, Optional

from .persistence import StateStore
from .updates import UpdateChecker, Updater

_logger = logging.getLogger("moreopenrepeater.updates")

POLL_SECONDS = 60
CHECK_EVERY_SECONDS = 30 * 60
RETRY_AFTER_SECONDS = 20 * 3600  # a try that failed without installing anything waits for the next night
DEFAULT_SETTINGS = {"enabled": False, "days": [0, 1, 2, 3, 4, 5, 6], "start": "02:00", "end": "05:00", "idle_minutes": 15}


def _time(value: str) -> day_time:
    hours, _, minutes = value.partition(":")
    return day_time(int(hours), int(minutes))


def in_window(settings: dict, now: datetime) -> bool:
    """A window that ends before it starts runs past midnight, and belongs
    to the day it starts on."""
    start, end, days = _time(settings["start"]), _time(settings["end"]), settings["days"]
    clock = now.time()
    if start < end:
        return now.weekday() in days and start <= clock < end
    yesterday = (now.weekday() - 1) % 7
    return (now.weekday() in days and clock >= start) or (yesterday in days and clock < end)


def next_window(settings: dict, now: datetime) -> Optional[datetime]:
    start = _time(settings["start"])
    for offset in range(8):
        day = now.date() + timedelta(days=offset)
        at = datetime.combine(day, start)
        if day.weekday() in settings["days"] and at > now:
            return at
    return None


class AutoUpdater:
    """`store=None` keeps it in memory (tests). `idle_seconds()` is how long
    the repeater has been quiet, 0 while it's in use."""

    def __init__(
        self,
        store: Optional[StateStore],
        updater: Updater,
        checker: UpdateChecker,
        channel: Callable[[], str],
        installed_sha: Callable[[], str],
        idle_seconds: Callable[[], float],
        clock: Callable[[], float] = time.time,
        wall_clock: Callable[[], datetime] = datetime.now,
    ) -> None:
        self._store = store
        self._updater = updater
        self._checker = checker
        self._channel = channel
        self._installed_sha = installed_sha
        self._idle_seconds = idle_seconds
        self._clock = clock
        self._wall_clock = wall_clock
        self.audit_hook: Callable[[str, str, str], None] = lambda actor, action, detail: None
        data = (store.load() if store is not None else None) or {}
        self.settings: dict = {**DEFAULT_SETTINGS, **data.get("settings", {})}
        self.state: dict = data.get("state", {})

    def _save(self) -> None:
        if self._store is not None:
            self._store.save({"settings": self.settings, "state": self.state})

    def update_settings(self, **changes: object) -> None:
        self.settings.update(changes)
        self._save()

    def status(self) -> dict:
        return {
            **self.settings,
            "last_check_at": self.state.get("last_check_at"),
            "last_result": self.state.get("last_result"),
            "next_window": next_window(self.settings, self._wall_clock()) if self.settings["enabled"] else None,
        }

    def _note(self, result: str) -> None:
        if result != self.state.get("last_result"):
            _logger.info("automatic update: %s", result)
        self.state["last_result"] = result
        self._save()

    def tick(self) -> None:
        if not self.settings["enabled"] or not self._updater.available:
            return
        status = self._updater.status() or {}
        if status.get("state") == "rolled_back" and status.get("to_sha"):
            self.state["rolled_back_sha"] = status["to_sha"]  # also a rollback after pressing Update
        if self._updater.busy() or not in_window(self.settings, self._wall_clock()):
            return
        now = self._clock()
        if now - self.state.get("last_check_at", 0) < CHECK_EVERY_SECONDS:
            return
        if self._idle_seconds() < self.settings["idle_minutes"] * 60:
            self._note("Waiting for the repeater to be quiet.")
            return
        channel = self._channel()
        self.state["last_check_at"] = now
        result = self._checker.check(channel, self._installed_sha(), refresh=True)
        if result.get("error"):
            self._note(result["error"])
            return
        latest = (result.get("latest") or {}).get("sha", "")
        if result.get("relation") != "ahead":
            self._note("Up to date." if result.get("relation") == "identical" else f"Nothing newer on {channel}.")
            return
        if latest == self.state.get("rolled_back_sha"):
            self._note(f"Skipping {latest[:7]}: it didn't start and was rolled back. Waiting for a newer version.")
            return
        if latest == self.state.get("attempted_sha") and now - self.state.get("attempted_at", 0) < RETRY_AFTER_SECONDS:
            self._note(f"{latest[:7]} didn't install; trying again in the next window.")
            return
        try:
            self._updater.request(channel)
        except OSError as error:
            self._note(f"Couldn't ask the updater: {error.strerror or error}")
            return
        self.state.update(attempted_sha=latest, attempted_at=now)
        self._note(f"Installing {latest[:7]} from {channel}.")
        self.audit_hook("automatic update", "Update", f"{channel} {latest[:7]}")
