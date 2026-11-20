"""The station's timezone: one setting that decides what "local time" means in every process.

Anything a person hears or reads as a wall-clock time (the spoken time, "19:30 on Tuesdays", the
hour a backup file is named for) uses the process's local time, `datetime.now()` and
`time.localtime()`. Those follow the `TZ` environment variable, so the station's timezone is
applied by setting `TZ` and calling `time.tzset()`. The core and the API are separate processes and
each applies it for itself (`follow`); nothing else has to know.

The setting is an IANA name such as `America/Phoenix`, or empty for "whatever this process started
with" (the container's `TZ`, which compose takes from `MOR_TZ`). Stored times, logs and event times
are not affected: they are UTC or epoch seconds.

Things that hold a computed local date or hour (the announcement schedule, the weather schedule,
the link and GPIO schedules) ask `generation()` and re-time themselves when it moves, so a change
never leaves them aiming at the old zone's clock. `shift_since` says by how much the clock moved.
"""
from __future__ import annotations

import logging
import os
import threading
import time
import zoneinfo
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Callable, Optional

_logger = logging.getLogger("moreopenrepeater.clock")

#: What this process started with. Empty setting means this; it is never read again after import.
_BASE_TZ: Optional[str] = os.environ.get("TZ")

_lock = threading.Lock()
_applied = ""  # the setting in force ("" = the process's own)
_shifts: list[timedelta] = []  # _shifts[n] is how far the clock moved going to generation n + 1

# Zones that exist in the database for old software but that nobody should be offered today.
_HIDDEN_PREFIXES = ("US/", "Etc/", "posix/", "right/", "SystemV/", "Brazil/", "Canada/", "Chile/", "Mexico/")
_SINGLE_NAMES_OFFERED = ("UTC",)

BAD_ZONE = "'{name}' is not a time zone this station knows. Use a name like America/Phoenix or Europe/London."


@lru_cache(maxsize=1)
def zone_names() -> frozenset[str]:
    """Every zone name the system can resolve (empty if the image carries no zone data)."""
    return frozenset(zoneinfo.available_timezones())


def offered_zone_names() -> list[str]:
    """The names a person should pick from, sorted: no legacy aliases (`US/Pacific`, `Etc/GMT+7`)."""
    return sorted(
        name for name in zone_names()
        if ("/" in name and not name.startswith(_HIDDEN_PREFIXES)) or name in _SINGLE_NAMES_OFFERED
    )


def check_timezone(name: object) -> str:
    """The setting to store: a zone name the system knows, or "" for the container's own.
    Raises ValueError with a message fit to show a person."""
    if name is None:
        return ""
    if not isinstance(name, str):
        raise ValueError("the time zone must be a name such as America/Phoenix")
    name = name.strip()
    if not name:
        return ""
    if name not in zone_names():
        raise ValueError(BAD_ZONE.format(name=name[:60]))
    return name


def _offset() -> timedelta:
    return datetime.now().astimezone().utcoffset() or timedelta(0)


def apply_timezone(name: object) -> bool:
    """Make this process's local time follow `name` ("" = what the process started with).
    True if that changed the zone in force. Raises ValueError for a name that is not a zone,
    and then changes nothing."""
    global _applied
    name = check_timezone(name)
    with _lock:
        if name == _applied:
            return False
        before = _offset()
        if name:
            os.environ["TZ"] = name
        elif _BASE_TZ is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = _BASE_TZ
        if hasattr(time, "tzset"):
            time.tzset()
        _applied = name
        _shifts.append(_offset() - before)
        _logger.info("the station's time zone is now %s", name or "the container's own")
        return True


def generation() -> int:
    """Rises by one every time the zone in force changes."""
    return len(_shifts)


def shift_since(seen: int) -> timedelta:
    """How far local time moved (new offset minus old) since `generation()` was `seen`."""
    return sum(_shifts[seen:], timedelta(0))


def applied_timezone() -> str:
    """The setting in force: a zone name, or "" for the container's own."""
    return _applied


@dataclass(frozen=True)
class DefaultZone:
    name: str  # "" when it cannot be told
    source: str  # "container" (the TZ variable), "system" (the machine's own files) or "unknown"


def default_zone() -> DefaultZone:
    """The zone this process would use with the setting empty, and where that came from."""
    base = (_BASE_TZ or "").lstrip(":")
    if base in zone_names():
        return DefaultZone(base, "container")
    if base:
        return DefaultZone("", "unknown")
    try:
        name = Path("/etc/timezone").read_text().strip()
        if name in zone_names():
            return DefaultZone(name, "system")
    except OSError:
        pass
    try:
        target = os.path.realpath("/etc/localtime")
        marker = "/zoneinfo/"
        if marker in target:
            name = target.split(marker, 1)[1]
            if name in zone_names():
                return DefaultZone(name, "system")
    except OSError:
        pass
    return DefaultZone("", "unknown")


def follow(
    add_config_listener: Callable[[Callable[[object], None]], None], initial: Optional[object] = None
) -> None:
    """Keep this process's local time on the saved `timezone` setting: apply `initial` (a
    configuration, if one is at hand) now, and again each time the service reports a
    configuration. A setting that does not apply is logged and the zone in force is kept."""

    def apply(config: object) -> None:
        try:
            apply_timezone(getattr(config, "timezone", ""))
        except ValueError:
            _logger.warning("the saved time zone is not one this system knows; keeping the current one")

    if initial is not None:
        apply(initial)
    add_config_listener(apply)


def reset() -> None:
    """Back to the process's own zone. For tests."""
    apply_timezone("")
