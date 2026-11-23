"""Weekly windows: a start time on some weekdays and a length in minutes, in
the station's local wall-clock time (naive datetimes, like announcements).

A schedule is a dict with "days" (0 = Monday), "time" ("HH:MM"), "minutes"
(0 = no end) and optionally "enabled".

Optional recurrence:
- every="weekly" (default): each matching weekday
- every="biweekly": matching weekdays on odd or even ISO weeks (`biweek_parity` 0/1)
- every="monthly": day-of-month `month_day` (1–28), ignoring `days`
"""
from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Optional


def _start_time(schedule: dict) -> time:
    hours, _, minutes = schedule["time"].partition(":")
    return time(int(hours), int(minutes))


def _every(schedule: dict) -> str:
    value = schedule.get("every") or "weekly"
    return value if value in ("weekly", "biweekly", "monthly") else "weekly"


def _matches(schedule: dict, start: datetime) -> bool:
    every = _every(schedule)
    if every == "monthly":
        day = int(schedule.get("month_day") or 0)
        return 1 <= day <= 28 and start.day == day
    if start.weekday() not in schedule.get("days", []):
        return False
    if every == "biweekly":
        parity = int(schedule.get("biweek_parity") or 0) % 2
        return start.isocalendar()[1] % 2 == parity
    return True


def _candidate_days(schedule: dict, around: datetime, offsets: range):
    every = _every(schedule)
    for offset in offsets:
        day = around.date() + timedelta(days=offset)
        start = datetime.combine(day, _start_time(schedule))
        if every == "monthly":
            if _matches(schedule, start):
                yield start
            continue
        if day.weekday() in schedule.get("days", []) and _matches(schedule, start):
            yield start


def last_start(schedule: dict, now: datetime) -> Optional[datetime]:
    span = range(0, -40, -1) if _every(schedule) == "monthly" else range(0, -15, -1)
    return next((start for start in _candidate_days(schedule, now, span) if start <= now), None)


def next_start(schedule: dict, now: datetime) -> Optional[datetime]:
    if not schedule.get("enabled", True):
        return None
    span = range(0, 40) if _every(schedule) == "monthly" else range(0, 15)
    return next((start for start in _candidate_days(schedule, now, span) if start > now), None)


def window_end(schedule: dict, start: datetime) -> Optional[datetime]:
    minutes = schedule.get("minutes", 0)
    return start + timedelta(minutes=minutes) if minutes else None


def starts_between(schedule: dict, begin: datetime, end: datetime) -> list[datetime]:
    """Schedule starts in [begin, end), for calendar views."""
    if not schedule.get("enabled", True):
        return []
    found: list[datetime] = []
    day = begin.date()
    last = end.date()
    while day <= last:
        for start in _candidate_days(schedule, datetime.combine(day, time.min), range(0, 1)):
            if begin <= start < end:
                found.append(start)
        day += timedelta(days=1)
    return found
