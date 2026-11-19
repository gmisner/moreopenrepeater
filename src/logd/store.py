"""The collector's event store: a bounded, searchable SQLite file (#221, #229).

Events are the project's `LogEvent`s. They are kept for 14 days, or 30 if they are errors or worse, and
never more than a size quota: when the file is over it the oldest routine events go first, then the oldest
of the rest. Text search is SQLite's FTS5 over the message, the event name and the context.

What is *missing* is recorded too, because a record with silent holes in it is worse than a smaller one.
Every process numbers its events and the number is checked here: a skipped number is a `sequence_gap` with
how many, a new boot id is a `restart`, a sender's own report of what it dropped is a `reported_drop`, and
what the quota removes is a `quota_pruned`. They are read with `gaps()`.
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from contracts import LogEvent

DEFAULT_MAX_BYTES = 64 * 1024 * 1024
DEFAULT_ROUTINE_DAYS = 14
DEFAULT_CRITICAL_DAYS = 30
ERROR = 40  # severity at which an event is kept for the longer period
MAX_PAGE = 1000
DEFAULT_PAGE = 200
QUERY_SECONDS = 5.0  # a query that takes longer is stopped
QUOTA_TARGET = 0.85  # a prune that is over the quota brings the file down to this share of it
QUOTA_ROUNDS = 6
MAX_VACUUM_CALLS = 100_000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    time REAL NOT NULL,
    severity INTEGER NOT NULL,
    module TEXT NOT NULL,
    module_version TEXT NOT NULL,
    boot_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    event TEXT NOT NULL,
    message TEXT,
    correlation_id TEXT,
    error_code TEXT,
    context TEXT NOT NULL,
    received REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS events_time ON events (time);
CREATE INDEX IF NOT EXISTS events_severity ON events (severity, id);
CREATE INDEX IF NOT EXISTS events_module ON events (module, id);
CREATE INDEX IF NOT EXISTS events_event ON events (event, id);
CREATE INDEX IF NOT EXISTS events_correlation ON events (correlation_id) WHERE correlation_id IS NOT NULL;
CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5(
    message, event, context, content='events', content_rowid='id', tokenize='unicode61'
);
CREATE TRIGGER IF NOT EXISTS events_ai AFTER INSERT ON events BEGIN
    INSERT INTO events_fts(rowid, message, event, context) VALUES (new.id, new.message, new.event, new.context);
END;
CREATE TRIGGER IF NOT EXISTS events_ad AFTER DELETE ON events BEGIN
    INSERT INTO events_fts(events_fts, rowid, message, event, context)
    VALUES ('delete', old.id, old.message, old.event, old.context);
END;
CREATE TABLE IF NOT EXISTS gaps (
    id INTEGER PRIMARY KEY,
    time REAL NOT NULL,
    kind TEXT NOT NULL,
    module TEXT NOT NULL,
    boot_id TEXT,
    count INTEGER NOT NULL,
    detail TEXT
);
CREATE INDEX IF NOT EXISTS gaps_time ON gaps (time);
"""

_INSERT = (
    "INSERT INTO events (time, severity, module, module_version, boot_id, seq, event, message, correlation_id,"
    " error_code, context, received) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)
_COLUMNS = "id, time, severity, module, module_version, boot_id, seq, event, message, correlation_id, error_code, context"


def _iso(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat()


def _like_prefix(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _fts_match(text: str) -> Optional[str]:
    """What a person typed, as an FTS5 query that cannot be a syntax error: every word is quoted, so AND, OR,
    NOT, NEAR, quotes, stars and colons are plain text, and all the words must be present. None when there is
    nothing in it that could match."""
    words = [w.replace('"', "") for w in text.split()]
    words = [w for w in words if re.search(r"\w", w)]
    return " ".join(f'"{w}"' for w in words) if words else None


class EventStore:
    def __init__(
        self,
        path: Optional[Path] = None,
        *,
        max_bytes: int = DEFAULT_MAX_BYTES,
        routine_days: float = DEFAULT_ROUTINE_DAYS,
        critical_days: float = DEFAULT_CRITICAL_DAYS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        """`path=None` keeps everything in memory (tests)."""
        self.path = path
        self.max_bytes = max_bytes
        self.routine_days = routine_days
        self.critical_days = critical_days
        self.clock = clock
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path) if path else ":memory:", check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._file = path is not None
        if self._file:
            if self._conn.execute("PRAGMA page_count").fetchone()[0] == 0:
                self._conn.execute("PRAGMA auto_vacuum = INCREMENTAL")  # only possible before the first table
            self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.execute("PRAGMA synchronous = NORMAL")  # fewer syncs: the SD card's wear, and a lost second is fine
        self._conn.executescript(_SCHEMA)
        # Queries on a file use their own connection, so a slow search does not hold up the writer.
        self._rconn = self._conn
        self._rlock = self._lock
        if self._file:
            self._rconn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False, isolation_level=None)
            self._rconn.row_factory = sqlite3.Row
            self._rlock = threading.RLock()
        self._last: dict[tuple[str, str], int] = {}  # (module, boot_id) -> highest sequence number seen
        self._boot: dict[str, str] = {}  # module -> the boot id it is on
        self._seen: dict[str, float] = {}  # module -> event time of the latest event
        self._load_trackers()

    def _load_trackers(self) -> None:
        """Pick up where each module was, so a collector that was down reports what it missed."""
        rows = self._conn.execute(
            "SELECT module, boot_id, MAX(seq) AS last_seq, MAX(time) AS latest FROM events GROUP BY module, boot_id"
        ).fetchall()
        for row in sorted(rows, key=lambda r: r["latest"]):  # the latest boot of each module wins
            self._boot[row["module"]] = row["boot_id"]
            self._last[(row["module"], row["boot_id"])] = row["last_seq"]
            self._seen[row["module"]] = row["latest"]

    # -- writing -----------------------------------------------------------------------------------------

    def insert_many(self, events: list[LogEvent]) -> int:
        """Store a batch in one transaction, with the gaps it reveals. All of it goes in or none, and the
        sequence tracking only moves if it did."""
        if not events:
            return 0
        now = self.clock()
        rows, gap_rows = [], []
        last, boot, seen = dict(self._last), dict(self._boot), dict(self._seen)
        for e in events:
            when = e.time.timestamp()
            key = (e.module, e.boot_id)
            previous_boot = boot.get(e.module)
            if previous_boot is None:
                boot[e.module], last[key] = e.boot_id, e.seq
            elif previous_boot != e.boot_id:
                gap_rows.append((now, "restart", e.module, e.boot_id, 0, previous_boot))
                boot[e.module], last[key] = e.boot_id, e.seq
            elif e.seq > last[key]:
                if e.seq > last[key] + 1:
                    gap_rows.append((now, "sequence_gap", e.module, e.boot_id, e.seq - last[key] - 1, None))
                last[key] = e.seq
            # a repeated or late number is stored but not counted: it is not a gap
            if e.event == "logging.dropped":
                dropped = e.context.get("dropped")
                if isinstance(dropped, int) and dropped > 0:
                    gap_rows.append((now, "reported_drop", e.module, e.boot_id, dropped, str(e.context.get("since"))))
            seen[e.module] = max(seen.get(e.module, 0.0), when)
            rows.append((
                when, int(e.severity), e.module, e.module_version, e.boot_id, e.seq, e.event, e.message,
                e.correlation_id, e.error_code, json.dumps(e.context, separators=(",", ":")), now,
            ))
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                self._insert_rows(rows)
                if gap_rows:
                    self._conn.executemany(
                        "INSERT INTO gaps (time, kind, module, boot_id, count, detail) VALUES (?, ?, ?, ?, ?, ?)", gap_rows
                    )
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
        self._last, self._boot, self._seen = last, boot, seen
        return len(rows)

    def _insert_rows(self, rows: list[tuple]) -> None:
        self._conn.executemany(_INSERT, rows)

    # -- reading ---------------------------------------------------------------------------------------------

    def query(
        self,
        *,
        since: Optional[float] = None,
        until: Optional[float] = None,
        min_severity: Optional[int] = None,
        module: Optional[str] = None,
        event: Optional[str] = None,
        correlation_id: Optional[str] = None,
        q: Optional[str] = None,
        before: Optional[int] = None,
        limit: int = DEFAULT_PAGE,
        max_limit: int = MAX_PAGE,
    ) -> dict[str, Any]:
        """Newest first, a page at a time. `before` is the `next_before` of the page before. `event` is exact,
        or a prefix when it ends in `.*`. `q` is words that must all appear in the message, name or context."""
        where, params = [], []
        if before is not None:
            where.append("id < ?"), params.append(before)
        if since is not None:
            where.append("time >= ?"), params.append(since)
        if until is not None:
            where.append("time < ?"), params.append(until)
        if min_severity is not None:
            where.append("severity >= ?"), params.append(min_severity)
        if module:
            where.append("module = ?"), params.append(module)
        if event:
            if event.endswith(".*"):
                where.append("event LIKE ? ESCAPE '\\'"), params.append(_like_prefix(event[:-1]))
            else:
                where.append("event = ?"), params.append(event)
        if correlation_id:
            where.append("correlation_id = ?"), params.append(correlation_id)
        if q:
            match = _fts_match(q)
            if match is None:
                where.append("0")
            else:
                where.append("id IN (SELECT rowid FROM events_fts WHERE events_fts MATCH ?)"), params.append(match)
        limit = max(1, min(int(limit), max_limit))
        sql = f"SELECT {_COLUMNS} FROM events" + (" WHERE " + " AND ".join(where) if where else "")
        sql += " ORDER BY id DESC LIMIT ?"
        rows = self._select(sql, [*params, limit + 1])
        more = len(rows) > limit
        rows = rows[:limit]
        return {"events": [self._event_dict(r) for r in rows], "next_before": rows[-1]["id"] if more and rows else None}

    def _select(self, sql: str, params: list) -> list[sqlite3.Row]:
        deadline = time.monotonic() + QUERY_SECONDS
        with self._rlock:
            self._rconn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)
            try:
                return self._rconn.execute(sql, params).fetchall()
            finally:
                self._rconn.set_progress_handler(None, 0)

    @staticmethod
    def _event_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"], "time": _iso(row["time"]), "severity": row["severity"], "module": row["module"],
            "module_version": row["module_version"], "boot_id": row["boot_id"], "seq": row["seq"],
            "event": row["event"], "message": row["message"], "correlation_id": row["correlation_id"],
            "error_code": row["error_code"], "context": json.loads(row["context"]),
        }

    def note_gap(self, kind: str, module: str, boot_id: Optional[str], count: int, detail: Optional[str] = None) -> None:
        """Record something the collector itself knows it lost (its queue overflowed, say)."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO gaps (time, kind, module, boot_id, count, detail) VALUES (?, ?, ?, ?, ?, ?)",
                [self.clock(), kind, module, boot_id, count, detail],
            )

    def gaps(self, limit: int = 100) -> list[dict[str, Any]]:
        """What is known to be missing from the record, newest first."""
        rows = self._select(
            "SELECT time, kind, module, boot_id, count, detail FROM gaps ORDER BY id DESC LIMIT ?", [max(1, min(limit, MAX_PAGE))]
        )
        return [
            {"time": _iso(r["time"]), "kind": r["kind"], "module": r["module"], "boot_id": r["boot_id"],
             "count": r["count"], "detail": r["detail"]}
            for r in rows
        ]

    def stats(self) -> dict[str, Any]:
        row = self._select(
            "SELECT COUNT(*) AS n, SUM(severity >= ?) AS errors, MIN(time) AS oldest, MAX(time) AS newest FROM events", [ERROR]
        )[0]
        return {
            "events": row["n"], "errors": row["errors"] or 0,
            "oldest": _iso(row["oldest"]) if row["oldest"] is not None else None,
            "newest": _iso(row["newest"]) if row["newest"] is not None else None,
            "used_bytes": self.used_bytes(), "quota_bytes": self.max_bytes,
            "retention": {"routine_days": self.routine_days, "critical_days": self.critical_days},
            "modules": {
                m: {"boot_id": b, "last_seq": self._last[(m, b)], "last_time": _iso(self._seen[m])}
                for m, b in self._boot.items()
            },
        }

    # -- keeping it bounded ----------------------------------------------------------------------------------

    def used_bytes(self) -> int:
        with self._lock:
            pages = self._conn.execute("PRAGMA page_count").fetchone()[0]
            free = self._conn.execute("PRAGMA freelist_count").fetchone()[0]
            size = self._conn.execute("PRAGMA page_size").fetchone()[0]
        return (pages - free) * size

    def prune(self) -> dict[str, int]:
        """Apply the retention, then the quota. Returns how many events each removed."""
        now = self.clock()
        day = 86400.0
        with self._lock:
            removed = self._delete("severity < ? AND time < ?", [ERROR, now - self.routine_days * day])
            removed += self._delete("severity >= ? AND time < ?", [ERROR, now - self.critical_days * day])
            self._conn.execute("DELETE FROM gaps WHERE time < ?", [now - self.critical_days * day])
            over = 0
            for _ in range(QUOTA_ROUNDS):
                used = self.used_bytes()
                if used <= self.max_bytes:
                    break
                total = self._conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
                if total == 0:
                    break  # nothing left to remove: the empty file is already over (a quota set absurdly low)
                # Remove the share of the events that is the share of the file over the target, which leaves
                # headroom so this is not needed again at once. The search index gives its space back only
                # when it is merged, so a delete-and-measure loop would never see the file get smaller.
                excess = (used - self.max_bytes * QUOTA_TARGET) / used
                wanted = max(1, min(total, int(total * excess) + 1))
                removed_now = self._delete_oldest("severity < ?", [ERROR], wanted)
                if removed_now < wanted:  # no routine events left to give: the oldest of the rest
                    removed_now += self._delete_oldest("1", [], wanted - removed_now)
                over += removed_now
                self._conn.execute("INSERT INTO events_fts(events_fts) VALUES ('optimize')")
                self._release_free_pages()
            if over:
                self._conn.execute(
                    "INSERT INTO gaps (time, kind, module, boot_id, count, detail) VALUES (?, 'quota_pruned', 'logd', NULL, ?, ?)",
                    [now, over, f"over the {self.max_bytes} byte quota"],
                )
            if removed or over:
                self._shrink()
        return {"retention": removed, "quota": over}

    def _delete(self, condition: str, params: list) -> int:
        return self._conn.execute(f"DELETE FROM events WHERE {condition}", params).rowcount

    def _delete_oldest(self, condition: str, params: list, count: int) -> int:
        return self._conn.execute(
            f"DELETE FROM events WHERE id IN (SELECT id FROM events WHERE {condition} ORDER BY id LIMIT ?)", [*params, count]
        ).rowcount

    def _release_free_pages(self) -> None:
        """Give freed pages back to the file system. Python's sqlite3 runs this pragma a different number of
        steps on different versions (one page a call on 3.11, all of them on 3.13), so go on until none are left."""
        if not self._file:
            return
        for _ in range(MAX_VACUUM_CALLS):
            if self._conn.execute("PRAGMA freelist_count").fetchone()[0] == 0:
                return
            self._conn.execute("PRAGMA incremental_vacuum(256)").fetchall()

    def _shrink(self) -> None:
        """Give freed pages back, and fold the write-ahead log into the file."""
        if self._file:
            self._release_free_pages()
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def close(self) -> None:
        with self._lock:
            if self._rconn is not self._conn:
                self._rconn.close()
            if self._file:
                try:
                    self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                except sqlite3.Error:
                    pass
            self._conn.close()
