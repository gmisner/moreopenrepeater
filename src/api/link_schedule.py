"""Links made on a schedule: a weekly net's hub linked at its start time and
dropped when it ends.

Each schedule is a node, the weekdays and start time (the station's local
wall clock, like announcements), how many minutes to stay linked (0 = stay
until someone disconnects it) and whether to link monitor only. Only links
the schedule made are dropped at the end: if the node was already linked,
it's left alone. Disconnecting a scheduled link by hand keeps it down for the
rest of that window. After a restart (app_rpt keeps its links), a link that's
up during its window is taken as the schedule's, so it's still dropped.
"""
from __future__ import annotations

import logging
from datetime import datetime, time, timedelta
from typing import Callable, Optional

from .links import LinkControl, LinkError

_logger = logging.getLogger("moreopenrepeater.link")

POLL_SECONDS = 15
STAY_GRACE = timedelta(minutes=5)  # "stay linked" schedules only connect this soon after the start


def _start_time(schedule: dict) -> time:
    hours, _, minutes = schedule["time"].partition(":")
    return time(int(hours), int(minutes))


def _starts(schedule: dict, around: datetime, offsets: range):
    days = schedule.get("days", [])
    for offset in offsets:
        day = around.date() + timedelta(days=offset)
        if day.weekday() in days:
            yield datetime.combine(day, _start_time(schedule))


def last_start(schedule: dict, now: datetime) -> Optional[datetime]:
    return next((start for start in _starts(schedule, now, range(0, -8, -1)) if start <= now), None)


def next_start(schedule: dict, now: datetime) -> Optional[datetime]:
    if not schedule.get("enabled", True):
        return None
    return next((start for start in _starts(schedule, now, range(0, 8)) if start > now), None)


def window_end(schedule: dict, start: datetime) -> Optional[datetime]:
    minutes = schedule.get("minutes", 0)
    return start + timedelta(minutes=minutes) if minutes else None


class LinkScheduler:
    def __init__(self, links: LinkControl, schedules: Callable[[], list[dict]]) -> None:
        self._links = links
        self._schedules = schedules
        self._handled: set[tuple[str, datetime]] = set()
        self._failed: set[tuple[str, datetime]] = set()
        self._ours: dict[str, datetime] = {}  # node -> when the schedule drops it
        self._started = False

    def until(self, node: str) -> Optional[datetime]:
        return self._ours.get(node)

    def status(self, now: datetime) -> list[dict]:
        """For each schedule, in order: its next start and, while its link is up, when it ends."""
        rows = []
        for schedule in self._schedules():
            until = self._ours.get(schedule["node"])
            start = last_start(schedule, now)
            active = until is not None and start is not None and window_end(schedule, start) == until
            rows.append({"node": schedule["node"], "next_start": next_start(schedule, now), "until": until if active else None})
        return rows

    async def tick(self, now: datetime) -> None:
        for node, end in list(self._ours.items()):
            if now >= end:
                del self._ours[node]
                await self._drop(node)
        for schedule in self._schedules():
            if schedule.get("enabled", True):
                await self._start(schedule, now, adopt=not self._started)
        self._started = True

    async def _start(self, schedule: dict, now: datetime, adopt: bool) -> None:
        start = last_start(schedule, now)
        if start is None:
            return
        end = window_end(schedule, start)
        if now >= (end or start + STAY_GRACE):
            return
        node = schedule["node"]
        key = (node, start)
        if key in self._handled:
            return
        try:
            if node in await self._links.linked():
                self._handled.add(key)
                if adopt and end is not None:
                    self._ours[node] = end
                return
            await self._links.connect(node, bool(schedule.get("monitor")))
        except LinkError as error:
            if key not in self._failed:
                self._failed.add(key)
                _logger.warning("couldn't link scheduled node %s: %s", node, error)
            return
        self._handled.add(key)
        self._forget_before(now)
        _logger.info("linked node %s on schedule%s", node, f" until {end:%H:%M}" if end else "")
        if end is not None:
            self._ours[node] = max(end, self._ours.get(node, end))

    async def _drop(self, node: str) -> None:
        try:
            if node in await self._links.linked():
                await self._links.disconnect(node)
                _logger.info("unlinked node %s at the end of its schedule", node)
        except LinkError as error:
            _logger.warning("couldn't unlink scheduled node %s: %s", node, error)

    def _forget_before(self, now: datetime) -> None:
        cutoff = now - timedelta(days=8)
        self._handled = {key for key in self._handled if key[1] > cutoff}
        self._failed = {key for key in self._failed if key[1] > cutoff}
