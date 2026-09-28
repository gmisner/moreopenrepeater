"""Airtime and usage statistics.

`ActivityRecorder` watches the controller's state and PTT changes and writes
one row per event to `ActivityStore` (SQLite -- append-only, potentially
many rows, and queried by time range, which is exactly what it's good at;
the small, rarely-written settings stay in state.json). `summarize` is a
pure function over those rows so the arithmetic is easy to test.

Row kinds:
  - "rx": a user transmission being repeated (duration; timed_out flag)
  - "tx": the transmitter keyed, for any reason (duration)
  - "id" / "announcement": something the repeater said on its own
  - "kerchunk": a key-up too short to pass the kerchunk filter (never repeated)
"""
from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from controller.state_machine import RECEIVING, TIMEOUT

KERCHUNK_SECONDS = 1.5  # shorter than this and nobody said anything
RETENTION_DAYS = 400
_SILENT_CLIPS = {"courtesy_tone", "timeout_tone"}


@dataclass(frozen=True)
class ActivityRow:
    kind: str
    started_at: float  # unix time
    duration: float
    timed_out: bool = False


class ActivityStore:
    """`path=None` keeps everything in memory (tests, or no data dir)."""

    def __init__(self, path: Optional[Path] = None) -> None:
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
        # One shared connection guarded by a lock: rows are written from the
        # event loop but summaries are read from FastAPI's worker threads.
        self._conn = sqlite3.connect(str(path) if path else ":memory:", check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock, self._conn:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS activity ("
                " kind TEXT NOT NULL, started_at REAL NOT NULL, duration REAL NOT NULL,"
                " timed_out INTEGER NOT NULL DEFAULT 0)"
            )
            self._conn.execute("CREATE INDEX IF NOT EXISTS activity_started_at ON activity (started_at)")

    def add(self, row: ActivityRow) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO activity (kind, started_at, duration, timed_out) VALUES (?, ?, ?, ?)",
                (row.kind, row.started_at, row.duration, int(row.timed_out)),
            )

    def rows(self, since: float, until: float, kind: Optional[str] = None) -> list[ActivityRow]:
        query = "SELECT kind, started_at, duration, timed_out FROM activity WHERE started_at >= ? AND started_at < ?"
        params: list = [since, until]
        if kind is not None:
            query += " AND kind = ?"
            params.append(kind)
        with self._lock:
            cursor = self._conn.execute(query + " ORDER BY started_at", params)
            return [ActivityRow(k, s, d, bool(t)) for k, s, d, t in cursor.fetchall()]

    def recent(self, kind: str, limit: int) -> list[ActivityRow]:
        with self._lock:
            cursor = self._conn.execute(
                "SELECT kind, started_at, duration, timed_out FROM activity WHERE kind = ?"
                " ORDER BY started_at DESC LIMIT ?",
                (kind, limit),
            )
            return [ActivityRow(k, s, d, bool(t)) for k, s, d, t in cursor.fetchall()]

    def prune(self, older_than: float) -> int:
        with self._lock, self._conn:
            return self._conn.execute("DELETE FROM activity WHERE started_at < ?", (older_than,)).rowcount


class ActivityRecorder:
    def __init__(self, store: ActivityStore) -> None:
        self.store = store
        self._rx_started: Optional[float] = None
        self._tx_started: Optional[float] = None

    def state_changed(self, old: str, new: str, now: float) -> None:
        if new == RECEIVING:
            self._rx_started = now
        elif old == RECEIVING and self._rx_started is not None:
            self.store.add(ActivityRow("rx", self._rx_started, now - self._rx_started, timed_out=new == TIMEOUT))
            self._rx_started = None

    def ptt_changed(self, active: bool, now: float) -> None:
        if active:
            self._tx_started = now
        elif self._tx_started is not None:
            self.store.add(ActivityRow("tx", self._tx_started, now - self._tx_started))
            self._tx_started = None

    def clip_played(self, clip: str, now: float) -> None:
        if clip in _SILENT_CLIPS:
            return
        self.store.add(ActivityRow("id" if clip == "id" else "announcement", now, 0.0))

    def kerchunk_filtered(self, now: float) -> None:
        self.store.add(ActivityRow("kerchunk", now, 0.0))


def summarize(rows: list[ActivityRow], since: datetime, until: datetime) -> dict:
    """Totals, plus user airtime by local hour of day and per local day."""
    rx = [r for r in rows if r.kind == "rx"]
    by_hour = [0.0] * 24
    days: dict[date, dict] = {}
    day = since.date()
    while day <= until.date():
        days[day] = {"date": day.isoformat(), "rx_seconds": 0.0, "tx_seconds": 0.0, "rx_count": 0}
        day += timedelta(days=1)

    for row in rows:
        started = datetime.fromtimestamp(row.started_at)
        bucket = days.get(started.date())
        if row.kind == "rx":
            by_hour[started.hour] += row.duration
            if bucket:
                bucket["rx_seconds"] += row.duration
                bucket["rx_count"] += 1
        elif row.kind == "tx" and bucket:
            bucket["tx_seconds"] += row.duration

    return {
        "since": since.isoformat(),
        "until": until.isoformat(),
        "rx_seconds": sum(r.duration for r in rx),
        "rx_count": len(rx),
        "kerchunks": sum(1 for r in rx if r.duration < KERCHUNK_SECONDS),
        "kerchunks_filtered": sum(1 for r in rows if r.kind == "kerchunk"),
        "timeouts": sum(1 for r in rx if r.timed_out),
        "longest_rx_seconds": max((r.duration for r in rx), default=0.0),
        "tx_seconds": sum(r.duration for r in rows if r.kind == "tx"),
        "ids": sum(1 for r in rows if r.kind == "id"),
        "announcements": sum(1 for r in rows if r.kind == "announcement"),
        "by_hour": by_hour,
        "by_day": list(days.values()),
    }
