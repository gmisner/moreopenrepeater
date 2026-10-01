"""Who changed what, and when.

Every state-changing API request is recorded (by middleware, so a new
route can't forget), along with actions dialed in over the air by DTMF.
Handlers can add a human-readable detail -- e.g. which settings changed.
Request bodies are never stored, so passwords can't end up here.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .persistence import copy_database, load_database, open_database

RETENTION_DAYS = 365


@dataclass(frozen=True)
class AuditEntry:
    at: float
    actor: str
    action: str
    detail: str
    status: int


class AuditLog:
    """`path=None` keeps everything in memory (tests)."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self._conn = open_database(path)
        self._lock = threading.Lock()
        with self._lock, self._conn:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS audit ("
                " at REAL NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL,"
                " detail TEXT NOT NULL DEFAULT '', status INTEGER NOT NULL DEFAULT 0)"
            )
            self._conn.execute("CREATE INDEX IF NOT EXISTS audit_at ON audit (at)")

    def record(self, at: float, actor: str, action: str, detail: str = "", status: int = 0) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO audit (at, actor, action, detail, status) VALUES (?, ?, ?, ?, ?)",
                (at, actor, action, detail, status),
            )

    def recent(self, limit: int = 200) -> list[AuditEntry]:
        with self._lock:
            cursor = self._conn.execute(
                "SELECT at, actor, action, detail, status FROM audit ORDER BY at DESC, rowid DESC LIMIT ?", (limit,)
            )
            return [AuditEntry(*row) for row in cursor.fetchall()]

    def prune(self, older_than: float) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM audit WHERE at < ?", (older_than,))

    def copy_to(self, path: Path) -> None:
        with self._lock:
            copy_database(self._conn, path)

    def replace_from(self, path: Path) -> None:
        with self._lock:
            load_database(self._conn, path)


def describe_config_change(before: dict, after: dict) -> str:
    changes = [f"{key}: {before.get(key)!r} → {value!r}" for key, value in after.items() if before.get(key) != value]
    return "; ".join(changes)
