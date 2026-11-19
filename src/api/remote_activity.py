"""The dashboard's view of the activity history when the core is a separate process (#229).

The core sees every transmission, so it records them (`core.build` gives it the history file) and
serves them on its control socket. This stands in for `ActivityStore` where the API reads them: same
`rows` and `recent`, answered by the core. Writing is the core's job, so `flush` and `prune` do
nothing here. A core that cannot be reached raises `CoreUnavailable`, so a page says so instead of
showing an empty history that looks like a quiet repeater.
"""
from __future__ import annotations

import logging
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Optional

import httpx

from contracts import ActivityRows

from .activity import ActivityRow
from .service.core_handle import CoreUnavailable

_logger = logging.getLogger("moreopenrepeater.remote_activity")

REQUEST_TIMEOUT_SECONDS = 10.0
MAX_PAGES = 100  # a window this long is a bug, not a repeater

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS activity ("
    " kind TEXT NOT NULL, started_at REAL NOT NULL, duration REAL NOT NULL,"
    " timed_out INTEGER NOT NULL DEFAULT 0)"
)


def _key(row: ActivityRow) -> tuple:
    return (row.kind, row.started_at, row.duration, row.timed_out)


class RemoteActivityStore:
    def __init__(self, socket_path: Path) -> None:
        self._socket_path = Path(socket_path)

    # -- reading ---------------------------------------------------------------------------

    def rows(self, since: float, until: float, kind: Optional[str] = None) -> list[ActivityRow]:
        """Every row from `since` up to `until`, oldest first. The core sends a page at a time; this
        asks again from where the last page ended until it has them all."""
        found: list[ActivityRow] = []
        start = since
        at_edge: Counter = Counter()  # rows already held that start exactly at `start`
        for _ in range(MAX_PAGES):
            params = {"since": repr(start), "until": repr(until)}
            if kind is not None:
                params["kind"] = kind
            page = self._ask(params)
            before = len(found)
            for entry in page.rows:
                row = ActivityRow(entry.kind, entry.started_at, entry.duration, entry.timed_out)
                if row.started_at == start and at_edge[_key(row)] > 0:
                    at_edge[_key(row)] -= 1  # the last page already gave this one
                    continue
                found.append(row)
            if not page.truncated:
                return found
            if len(found) == before:
                _logger.warning("the core's activity history has more rows at one instant than a page holds")
                return found
            start = found[-1].started_at
            at_edge = Counter(_key(r) for r in found if r.started_at == start)
        _logger.warning("the activity history for this window is longer than %d pages; the oldest part is shown", MAX_PAGES)
        return found

    def recent(self, kind: str, limit: int) -> list[ActivityRow]:
        """The newest `limit` rows of one kind, newest first."""
        page = self._ask({"kind": kind, "limit": str(max(1, limit)), "newest": "1"})
        return [ActivityRow(e.kind, e.started_at, e.duration, e.timed_out) for e in page.rows]

    def _ask(self, params: dict) -> ActivityRows:
        try:
            with httpx.Client(
                transport=httpx.HTTPTransport(uds=str(self._socket_path)), base_url="http://core",
                timeout=REQUEST_TIMEOUT_SECONDS,
            ) as client:
                response = client.get("/activity", params=params)
        except httpx.ConnectError:
            raise CoreUnavailable("the core is not reachable, so its activity history cannot be read") from None
        except httpx.TimeoutException:
            raise CoreUnavailable("the core did not answer the request for its activity history") from None
        except httpx.HTTPError:
            raise CoreUnavailable("the connection to the core failed while reading its activity history") from None
        if response.status_code != 200:
            raise CoreUnavailable(f"the core answered {response.status_code} to the request for its activity history")
        try:
            return ActivityRows.model_validate_json(response.content)
        except ValueError:
            raise CoreUnavailable("the core's answer about its activity history was not understood") from None

    # -- what the core does for itself -------------------------------------------------------

    def flush(self) -> None:
        """The core writes its own history."""

    def prune(self, older_than: float) -> int:
        """The core drops its own old rows."""
        return 0

    # -- backups -----------------------------------------------------------------------------

    def copy_to(self, path: Path) -> None:
        """A backup archive has a history file for the activity. The history stays with the core, in
        its own data folder, so the archive gets an empty one of the right shape."""
        connection = sqlite3.connect(str(path))
        try:
            with connection:
                connection.execute(_SCHEMA)
        finally:
            connection.close()

    def replace_from(self, path: Path) -> None:
        """Restoring a backup cannot put history back into the core from here."""
        _logger.warning("the restored activity history was not applied: the core keeps its own history")
