"""Sessions and counters for the Winlink page (ADR 0006): what the gateway and the modem say about themselves.

The API has no Docker socket and must not get one, so it cannot read the two containers' logs. They write a small JSON snapshot each
(docker/winlink-reporter.py) into an in-memory volume that this process only reads: `gateway.json` (counters and the last sessions,
from LinBPQ's own session log) and `modem.json` (frames heard and sent, from Dire Wolf's output). Nothing is written back and nothing
is kept on disk here.

Rules this module keeps, because a dead gateway or a bad file must never slow the dashboard or the radio:

- The files are not trusted. Each one is size-limited, checked field by field, and rebuilt from the checked values: a number is a
  small non-negative integer, a callsign must look like one, an outcome is one of three words. Nothing else is passed on, so
  nothing a remote station typed (its software name, its locator) or any text can reach the page.
- A file that is not fresh is not shown as current. The reporters rewrite theirs at least every 30 seconds.
- The read runs off the event loop with a hard timeout, callers that arrive together share it, and the answer is kept for
  `CACHE_SECONDS`, like the status checks next to it.
- A session that ends is logged once as `winlink.session_ended`, with its length, message counts and outcome, and without the
  station's callsign. Sessions already there when the API started are not announced. The log is written when the page (or anything
  else) next reads this, so with nobody looking it appears at the next look.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from observability import log_event

_logger = logging.getLogger("moreopenrepeater.winlink")

CACHE_SECONDS = 15.0
STALE_SECONDS = 90.0  # the reporters rewrite their file at least every 30 s
READ_TIMEOUT_SECONDS = 2.0
MAX_FILE_BYTES = 256 * 1024
MAX_ROWS = 100  # what the reporter keeps
SESSIONS_SHOWN = 25
MAX_COUNT = 10_000_000
MAX_SECONDS = 7 * 24 * 3600
MAX_SEEN = 1000
GATEWAY_FILE = "gateway.json"
MODEM_FILE = "modem.json"

_CALLSIGN = re.compile(r"^[A-Z0-9]{3,7}(-([0-9]|1[0-5]))?$")
_ID = re.compile(r"^[0-9a-f]{12}$")
_OUTCOMES = ("completed", "ended_early", "unknown")
_YEAR_2000, _YEAR_2100 = 946684800, 4102444800

COUNTER_NAMES = ("incomplete", "messages_in", "messages_out", "bytes_in", "bytes_out", "connects", "failed_connects")


def _count(value: object) -> int:
    """A small non-negative whole number, or 0. A bool, a float or a string is not a count."""
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(0, min(value, MAX_COUNT))


def _stamp(value: object) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if _YEAR_2000 <= value <= _YEAR_2100 else None


def _iso(value: Optional[float]) -> Optional[str]:
    return None if value is None else datetime.fromtimestamp(value, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _session(row: object) -> Optional[dict]:
    if not isinstance(row, dict):
        return None
    ident, start, end = row.get("id"), _stamp(row.get("start")), _stamp(row.get("end"))
    if not (isinstance(ident, str) and _ID.match(ident)) or start is None or end is None:
        return None
    call = row.get("callsign")
    call = call.strip().upper() if isinstance(call, str) else ""
    outcome = row.get("outcome")
    seconds = row.get("duration_s")
    return {
        "id": ident,
        "callsign": call if _CALLSIGN.match(call) else None,
        "start": start, "end": end,
        "duration_s": 0 if isinstance(seconds, bool) or not isinstance(seconds, int) else max(0, min(seconds, MAX_SECONDS)),
        "messages_in": _count(row.get("messages_in")), "messages_out": _count(row.get("messages_out")),
        "bytes_in": _count(row.get("bytes_in")), "bytes_out": _count(row.get("bytes_out")),
        "outcome": outcome if outcome in _OUTCOMES else "unknown",
    }


class WinlinkActivity:
    """The cached, never-blocking answer to "what has the gateway been doing?"."""

    def __init__(
        self,
        status_dir,
        *,
        clock: Callable[[], float] = time.time,
        cache_seconds: float = CACHE_SECONDS,
        stale_seconds: float = STALE_SECONDS,
        timeout: float = READ_TIMEOUT_SECONDS,
    ) -> None:
        self.status_dir = Path(status_dir) if status_dir else None
        self._clock = clock
        self.cache_seconds = cache_seconds
        self.stale_seconds = stale_seconds
        self.timeout = timeout
        self._cached: Optional[tuple[float, Optional[dict]]] = None
        self._running: Optional[asyncio.Future] = None
        self._seen: dict[str, None] = {}
        self._baselined = False
        self._born = clock()  # a gateway that started after this did not have its sessions before we looked: all of them are new

    async def read(self) -> Optional[dict]:
        """The `activity` part of the status answer, or None when this install has no status folder."""
        if self.status_dir is None:
            return None
        now = self._clock()
        cached = self._cached
        if cached is not None and 0 <= now - cached[0] < self.cache_seconds:
            return cached[1]
        if self._running is None or self._running.done():
            self._running = asyncio.get_running_loop().run_in_executor(None, self._load_all)
        try:
            # Shielded: a client that hangs up must not cancel the read other callers are waiting for.
            raw = await asyncio.wait_for(asyncio.shield(self._running), self.timeout)
        except asyncio.TimeoutError:
            return {"gateway": None, "modem": None, "notes": ["Reading the gateway's and the modem's reports took too long."]}
        if not isinstance(raw, dict):
            return {"gateway": None, "modem": None, "notes": ["Reading the gateway's and the modem's reports failed."]}
        found = raw.pop("_rows", None)
        if found is not None:
            self._announce(*found)
        self._cached = (self._clock(), raw)
        return raw

    # -- reading and checking the two files (runs in a worker thread) -----------------------------------------------------

    def _load(self, name: str, kind: str, label: str) -> tuple[Optional[dict], Optional[str]]:
        """The checked contents of one file, or (None, what to tell the person)."""
        assert self.status_dir is not None
        try:
            with open(self.status_dir / name, "rb") as handle:
                data = handle.read(MAX_FILE_BYTES + 1)
        except FileNotFoundError:
            return None, f"The {label} has not reported yet. Its container may not be running."
        except OSError:
            return None, f"The {label}'s report could not be read."
        if len(data) > MAX_FILE_BYTES:
            return None, f"The {label}'s report could not be read."
        try:
            parsed = json.loads(data)
        except ValueError:
            return None, f"The {label}'s report could not be read."
        if not isinstance(parsed, dict) or parsed.get("v") != 1 or parsed.get("kind") != kind:
            return None, f"The {label}'s report could not be read."
        updated = _stamp(parsed.get("updated"))
        if updated is None:
            return None, f"The {label}'s report could not be read."
        if abs(self._clock() - updated) > self.stale_seconds:
            return None, f"The {label} is not reporting: its last report is too old, so it may have stopped."
        return parsed, None

    def _load_all(self) -> dict:
        notes: list[str] = []
        gateway, why = self._load(GATEWAY_FILE, "gateway", "gateway")
        if why:
            notes.append(why)
        modem, why = self._load(MODEM_FILE, "modem", "modem")
        if why:
            notes.append(why)
        result: dict = {"gateway": None, "modem": None, "notes": notes}
        if gateway is not None:
            built = self._gateway(gateway)
            if built is None:
                notes.append("The gateway's report could not be read.")
            else:
                result["gateway"], result["_rows"] = built[0], (built[1], _stamp(gateway.get("started")))
        if modem is not None:
            built_modem = self._modem(modem)
            if built_modem is None:
                notes.append("The modem's report could not be read.")
            else:
                result["modem"] = built_modem
        return result

    def _gateway(self, data: dict) -> Optional[tuple[dict, list[dict]]]:
        counters, rows = data.get("counters"), data.get("sessions")
        if not isinstance(counters, dict) or not isinstance(rows, list):
            return None
        sessions = [s for s in (_session(row) for row in rows[:MAX_ROWS]) if s is not None]
        sessions.sort(key=lambda s: s["end"], reverse=True)
        today = datetime.fromtimestamp(self._clock()).date()  # the station's own day: the process follows its time zone setting
        todays = [s for s in sessions if datetime.fromtimestamp(s["end"]).date() == today]
        full = len(rows) >= MAX_ROWS and bool(sessions) and datetime.fromtimestamp(sessions[-1]["end"]).date() == today
        out = {
            "sessions_today": len(todays), "sessions_today_is_minimum": full, "sessions_total": _count(counters.get("sessions")),
            **{name: _count(counters.get(name)) for name in COUNTER_NAMES},
            "last_session_at": _iso(_stamp(counters.get("last_session_end"))), "since": _iso(_stamp(data.get("started"))),
        }
        shown = [
            {"id": s["id"], "callsign": s["callsign"], "started_at": _iso(s["start"]), "duration_s": s["duration_s"],
             "messages_in": s["messages_in"], "messages_out": s["messages_out"], "bytes_in": s["bytes_in"],
             "bytes_out": s["bytes_out"], "outcome": s["outcome"]}
            for s in sessions[:SESSIONS_SHOWN]
        ]
        return {"counters": out, "sessions": shown}, sessions

    def _modem(self, data: dict) -> Optional[dict]:
        counters = data.get("counters")
        if not isinstance(counters, dict):
            return None
        return {"counters": {
            "frames_heard": _count(counters.get("frames_heard")), "frames_sent": _count(counters.get("frames_sent")),
            "last_heard_at": _iso(_stamp(counters.get("last_heard"))), "last_sent_at": _iso(_stamp(counters.get("last_sent"))),
            "since": _iso(_stamp(data.get("started"))),
        }}

    # -- the event -----------------------------------------------------------------------------------------------------------

    def _announce(self, rows: list[dict], started: Optional[float]) -> None:
        fresh = [row for row in sorted(rows, key=lambda r: r["end"]) if row["id"] not in self._seen]
        for row in fresh:
            self._seen[row["id"]] = None
        while len(self._seen) > MAX_SEEN:
            self._seen.pop(next(iter(self._seen)))
        # The first look at a gateway that was already running only learns what is there. One that started after this did has
        # nothing old in it.
        announce = self._baselined or (started is not None and started >= self._born)
        self._baselined = True
        if not announce:
            return
        for row in fresh:
            log_event(
                _logger, logging.INFO, "winlink.session_ended", "A Winlink session ended",
                duration_s=row["duration_s"], messages_in=row["messages_in"], messages_out=row["messages_out"],
                bytes_in=row["bytes_in"], bytes_out=row["bytes_out"], outcome=row["outcome"], facts_in_text=False,
            )
