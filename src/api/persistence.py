"""Durable storage for everything the dashboard can edit (config, macros,
announcements) as a single JSON file.

A JSON file rather than a database, matching `api.assets`: the state is
small, written only when a human saves something, and a plain file is easy
to inspect, back up, or hand-edit on a Pi. Writes go to a temp file that is
then atomically renamed over the real one, so a power cut mid-write (common
on a Pi yanked from the wall) leaves either the old or the new state, never
a truncated file.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Optional

_logger = logging.getLogger("moreopenrepeater.persistence")


def open_database(path: Optional[Path]) -> sqlite3.Connection:
    """A connection shared between the event loop and worker threads (so
    callers hold a lock). `path=None` is in memory. On disk it uses a
    write-ahead log without an fsync per commit, which writes the SD card
    far less; a power cut can lose the last moments, never the database."""
    if path is None:
        return sqlite3.connect(":memory:", check_same_thread=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def copy_database(conn: sqlite3.Connection, path: Path) -> None:
    """Write a consistent copy of a live SQLite database to `path`, as a
    single file whatever the live one's journal mode."""
    dest = sqlite3.connect(str(path))
    try:
        conn.backup(dest)
        dest.execute("PRAGMA journal_mode=DELETE")
    finally:
        dest.close()


def load_database(conn: sqlite3.Connection, path: Path) -> None:
    """Replace a live database's contents with those of the file at `path`."""
    source = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        source.backup(conn)
    finally:
        source.close()


def database_has_table(path: Path, table: str) -> bool:
    """Whether `path` is an intact SQLite database with `table` in it."""
    try:
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        try:
            if conn.execute("PRAGMA quick_check").fetchone() != ("ok",):
                return False
            return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone() is not None
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        return False


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> Optional[dict]:
        if not self.path.exists():
            return None
        try:
            return json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError):
            # Keep the unreadable file for a human to look at instead of
            # silently overwriting it with defaults on the next save.
            backup = self.path.with_suffix(self.path.suffix + ".corrupt")
            os.replace(self.path, backup)
            _logger.exception("could not read %s; moved it to %s and starting from defaults", self.path, backup)
            return None

    def save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=self.path.parent, prefix=self.path.name, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_name, self.path)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise
