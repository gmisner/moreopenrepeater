"""Saved recordings of repeated transmissions, as WAV files.

One file per transmission, named by its start time in milliseconds, so the
directory itself is the index -- no database needed for a list that's only
ever shown newest-first and pruned by age. `directory=None` disables
storage (tests, or no data directory).
"""
from __future__ import annotations

import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

from playout.wav import encode_wav

from .safe_paths import child_path

_WAV_HEADER_BYTES = 44
_ID = re.compile(r"^\d{13,}$")


@dataclass(frozen=True)
class RecordingInfo:
    id: str
    started_at: float  # unix time
    duration: float


class RecordingStore:
    def __init__(self, directory: Optional[Path], sample_rate: int = 16000) -> None:
        self.directory = directory
        self.sample_rate = sample_rate
        if directory is not None:
            directory.mkdir(parents=True, exist_ok=True)

    @property
    def enabled(self) -> bool:
        return self.directory is not None

    def path_for(self, recording_id: str) -> Path:
        if self.directory is None or not _ID.match(recording_id):
            raise KeyError(recording_id)
        return child_path(self.directory, f"{recording_id}.wav")

    def transcript_path(self, recording_id: str) -> Path:
        return self.path_for(recording_id).with_suffix(".txt")

    def recent_ids(self, limit: int) -> list[str]:
        return [r.id for r in self.list(limit)]

    def save(self, samples: np.ndarray, started_at: float) -> Optional[RecordingInfo]:
        if self.directory is None:
            return None
        recording_id = str(int(started_at * 1000))
        path = self.path_for(recording_id)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(encode_wav(samples, self.sample_rate))
        tmp.replace(path)
        return self._info(path)

    def list(self, limit: int = 50) -> list[RecordingInfo]:
        if self.directory is None:
            return []
        paths = sorted((p for p in self.directory.glob("*.wav") if _ID.match(p.stem)), reverse=True)
        return [self._info(p) for p in paths[:limit]]

    def paths(self) -> list[Path]:
        if self.directory is None:
            return []
        return sorted(p for p in self.directory.glob("*.wav") if _ID.match(p.stem))

    def import_file(self, source: Path) -> bool:
        """Adds a recording file (e.g. from a backup) unless it's already here."""
        path = self.path_for(source.stem)
        if path.exists():
            return False
        tmp = path.with_suffix(".tmp")
        shutil.copyfile(source, tmp)
        tmp.replace(path)
        return True

    def delete(self, recording_id: str) -> bool:
        try:
            self.path_for(recording_id).unlink()
        except (KeyError, FileNotFoundError):
            return False
        self.transcript_path(recording_id).unlink(missing_ok=True)
        return True

    def prune(self, older_than: float) -> int:
        cutoff = int(older_than * 1000)
        removed = 0
        if self.directory is not None:
            for path in self.directory.glob("*.wav"):
                if _ID.match(path.stem) and int(path.stem) < cutoff:
                    path.unlink(missing_ok=True)
                    path.with_suffix(".txt").unlink(missing_ok=True)
                    removed += 1
        return removed

    def prune_days(self, days: float) -> int:
        return self.prune(time.time() - days * 86400)

    def _info(self, path: Path) -> RecordingInfo:
        size = path.stat().st_size
        return RecordingInfo(
            id=path.stem,
            started_at=int(path.stem) / 1000,
            duration=max(0, size - _WAV_HEADER_BYTES) / 2 / self.sample_rate,
        )
