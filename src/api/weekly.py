"""Weekly windows: a start time on some weekdays and a length in minutes, in
the station's local wall-clock time (naive datetimes, like announcements).

A schedule is a dict with "days" (0 = Monday), "time" ("HH:MM"), "minutes"
(0 = no end) and optionally "enabled".
"""
from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Optional


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
