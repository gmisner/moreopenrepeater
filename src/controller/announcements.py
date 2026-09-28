"""Scheduled announcements: what to say, and when.

Pure logic on naive local wall-clock datetimes -- no threads, no sleeping --
so it's driven from tests with plain datetimes. The service polls `due()`
and hands the resulting clips to the controller's announcement queue, which
decides when the channel is actually clear to transmit.

Times are local wall-clock (naive) because "the net reminder at 19:30"
means 19:30 on the station's clock, including across DST changes.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Literal, Optional

ALL_DAYS = [0, 1, 2, 3, 4, 5, 6]  # Monday = 0, matching datetime.weekday()


@dataclass(frozen=True)
class Announcement:
    id: str
    name: str
    message: str = ""
    asset_id: Optional[str] = None
    kind: Literal["interval", "weekly"] = "interval"
    every_minutes: int = 60
    times: list[str] = field(default_factory=list)  # "HH:MM", for kind="weekly"
    days: list[int] = field(default_factory=lambda: list(ALL_DAYS))
    enabled: bool = True


def parse_time_of_day(value: str) -> time:
    hours, _, minutes = value.partition(":")
    return time(int(hours), int(minutes))


def next_occurrence(announcement: Announcement, after: datetime) -> Optional[datetime]:
    """When `announcement` should next play, strictly after `after`."""
    if not announcement.enabled:
        return None
    if announcement.kind == "interval":
        return after + timedelta(minutes=announcement.every_minutes)
    times = sorted(parse_time_of_day(t) for t in announcement.times)
    if not times or not announcement.days:
        return None
    for day_offset in range(8):
        day = after.date() + timedelta(days=day_offset)
        if day.weekday() not in announcement.days:
            continue
        for time_of_day in times:
            candidate = datetime.combine(day, time_of_day)
            if candidate > after:
                return candidate
    return None


class AnnouncementScheduler:
    def __init__(self, announcements: list[Announcement], now: datetime) -> None:
        self._announcements: dict[str, Announcement] = {}
        self._next_run: dict[str, Optional[datetime]] = {}
        self.set_announcements(announcements, now)

    def list(self) -> list[Announcement]:
        return list(self._announcements.values())

    def next_run(self, announcement_id: str) -> Optional[datetime]:
        return self._next_run.get(announcement_id)

    def set_announcements(self, announcements: list[Announcement], now: datetime) -> None:
        """Replace the whole set. Unchanged announcements keep their next run
        time (so editing one doesn't restart every interval timer); new or
        edited ones are scheduled from `now`."""
        previous = self._announcements
        self._announcements = {a.id: a for a in announcements}
        self._next_run = {
            a.id: self._next_run[a.id] if previous.get(a.id) == a else next_occurrence(a, now)
            for a in announcements
        }

    def upsert(self, announcement: Announcement, now: datetime) -> None:
        others = [a for a in self._announcements.values() if a.id != announcement.id]
        self.set_announcements(others + [announcement], now)

    def remove(self, announcement_id: str, now: datetime) -> None:
        self.set_announcements([a for a in self._announcements.values() if a.id != announcement_id], now)

    def due(self, now: datetime) -> list[Announcement]:
        """Announcements whose time has come, each rescheduled from `now` --
        so if several runs were missed (the machine slept), it plays once
        rather than once per missed run."""
        fired = []
        for announcement_id, run_at in self._next_run.items():
            if run_at is not None and now >= run_at:
                announcement = self._announcements[announcement_id]
                fired.append(announcement)
                self._next_run[announcement_id] = next_occurrence(announcement, now)
        return fired


def announcement_from_dict(data: dict) -> Announcement:
    known = {f.name for f in dataclasses.fields(Announcement)}
    return Announcement(**{k: v for k, v in data.items() if k in known})
