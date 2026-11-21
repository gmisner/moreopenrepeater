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
from datetime import datetime, timedelta
from typing import Callable, Optional

from controller import station_clock

from observability import log_event

from .links import LinkControl, LinkError
from .weekly import last_start, next_start, window_end

_logger = logging.getLogger("moreopenrepeater.link")

POLL_SECONDS = 15
STAY_GRACE = timedelta(minutes=5)  # "stay linked" schedules only connect this soon after the start


class LinkScheduler:
    def __init__(self, links: LinkControl, schedules: Callable[[], list[dict]]) -> None:
        self._links = links
        self._schedules = schedules
        self._handled: set[tuple[str, datetime]] = set()
        self._failed: set[tuple[str, datetime]] = set()
        self._ours: dict[str, datetime] = {}  # node -> when the schedule drops it
        self._started = False
        self._zone_seen = station_clock.generation()

    def _follow_zone(self) -> None:
        """A time zone change moves the clock the windows are written in. Links this made keep
        their end at the same moment. (Windows already handled are not forgotten: a node dropped by hand
        stays down for the rest of its window.)"""
        seen = self._zone_seen
        if seen == station_clock.generation():
            return
        self._zone_seen = station_clock.generation()
        delta = station_clock.shift_since(seen)
        self._ours = {node: end + delta for node, end in self._ours.items()}

    def until(self, node: str) -> Optional[datetime]:
        return self._ours.get(node)

    def status(self, now: datetime) -> list[dict]:
        """For each schedule, in order: its next start and, while its link is up, when it ends."""
        self._follow_zone()
        rows = []
        for schedule in self._schedules():
            until = self._ours.get(schedule["node"])
            start = last_start(schedule, now)
            active = until is not None and start is not None and window_end(schedule, start) == until
            rows.append({"node": schedule["node"], "next_start": next_start(schedule, now), "until": until if active else None})
        return rows

    async def tick(self, now: datetime) -> None:
        self._follow_zone()
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
                log_event(
                    _logger, logging.WARNING, "link.schedule_failed", f"couldn't link scheduled node {node}: {error}",
                    error_code="link.schedule", action="link", node=node, exception=type(error).__name__, facts_in_text=False,
                )
            return
        self._handled.add(key)
        self._forget_before(now)
        log_event(
            _logger, logging.INFO, "link.schedule_applied", f"linked node {node} on schedule{f' until {end:%H:%M}' if end else ''}",
            action="link", node=node, facts_in_text=False,
        )
        if end is not None:
            self._ours[node] = max(end, self._ours.get(node, end))

    async def _drop(self, node: str) -> None:
        try:
            if node in await self._links.linked():
                await self._links.disconnect(node)
                log_event(
                    _logger, logging.INFO, "link.schedule_applied", f"unlinked node {node} at the end of its schedule",
                    action="unlink", node=node, facts_in_text=False,
                )
        except LinkError as error:
            log_event(
                _logger, logging.WARNING, "link.schedule_failed", f"couldn't unlink scheduled node {node}: {error}",
                error_code="link.schedule", action="unlink", node=node, exception=type(error).__name__, facts_in_text=False,
            )

    def _forget_before(self, now: datetime) -> None:
        cutoff = now - timedelta(days=8)
        self._handled = {key for key in self._handled if key[1] > cutoff}
        self._failed = {key for key in self._failed if key[1] > cutoff}
