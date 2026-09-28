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
import tempfile
from pathlib import Path
from typing import Optional

_logger = logging.getLogger("moreopenrepeater.persistence")


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
