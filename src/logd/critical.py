"""A small on-disk file of errors, for installs that keep the main history in memory (#221, #229).

With `--protect-sd` logs live in RAM to spare the SD card, and after a power cut the reason for it is gone.
Errors are rare, so they are the one thing worth a few kilobytes of writes: every event at error severity or
worse is also appended here as a JSON line. The file is bounded (it is renamed to `.1` when it reaches its
size, replacing the previous `.1`, so at most twice that size is ever kept) and a write that fails is
counted and never raised: the collector's job of keeping the radio's logs flowing does not depend on it.
When the collector starts with an empty history, the file's events are put back so the History tab shows
what happened before the restart.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Iterable

from contracts import LogEvent

_logger = logging.getLogger("moreopenrepeater.logd")

FILE_NAME = "critical-events.jsonl"
DEFAULT_MAX_BYTES = 1024 * 1024
ERROR = 40


class CriticalFile:
    def __init__(self, directory: Path, *, max_bytes: int = DEFAULT_MAX_BYTES) -> None:
        self.directory = directory
        self.path = directory / FILE_NAME
        self.older = directory / (FILE_NAME + ".1")
        self.max_bytes = max_bytes
        self.written = 0
        self.write_errors = 0
        self.restored = 0
        self._warned = False

    def append(self, events: Iterable[LogEvent]) -> None:
        """Add the errors among `events`. Never raises."""
        lines = [e.to_json_line() for e in events if int(e.severity) >= ERROR]
        if not lines:
            return
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            if self.path.exists() and self.path.stat().st_size >= self.max_bytes:
                os.replace(self.path, self.older)
            with open(self.path, "a+b") as handle:
                lead = b""
                if handle.tell():  # a power cut can leave half a line: start on a new one, or the next error is lost with it
                    handle.seek(-1, os.SEEK_END)
                    lead = b"" if handle.read(1) == b"\n" else b"\n"
                handle.write(lead + ("\n".join(lines) + "\n").encode("utf-8"))
            self.written += len(lines)
        except OSError as error:
            self.write_errors += 1
            if not self._warned:  # once: a full or missing disk would otherwise fill the log with this
                self._warned = True
                _logger.warning("could not write the critical events file %s: %s", self.path, type(error).__name__)

    def load(self) -> list[LogEvent]:
        """Every event the file holds, oldest first. A damaged or half-written line is skipped."""
        events: list[LogEvent] = []
        for source in (self.older, self.path):
            try:
                text = source.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for line in text.splitlines():
                if not line.strip():
                    continue
                try:
                    events.append(LogEvent.model_validate_json(line))
                except ValueError:
                    continue
        return events

    def stats(self) -> dict:
        size = 0
        for source in (self.path, self.older):
            try:
                size += source.stat().st_size
            except OSError:
                pass
        return {
            "directory": str(self.directory), "bytes": size, "max_bytes": self.max_bytes * 2,
            "written": self.written, "write_errors": self.write_errors, "restored": self.restored,
        }
