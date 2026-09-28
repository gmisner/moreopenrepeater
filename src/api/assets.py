"""Filesystem-backed storage for uploaded audio clips (courtesy tones, IDs, etc.).

No database -- assets live under a data directory as `<id>.wav` with a JSON
sidecar index for metadata, consistent with the project's general avoidance
of a database for state this small and low-write-volume.

This only manages storage; actually playing a selected asset during a
transmission is out of scope until `audio_io`/`link` are wired into
`RepeaterService` (already an open item -- see the README).
"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

AssetKind = Literal["courtesy_tone", "id", "timeout_tone", "custom"]


@dataclass(frozen=True)
class AssetInfo:
    id: str
    kind: AssetKind
    filename: str
    uploaded_at: str


class AudioAssetStore:
    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._index_path = self._data_dir / "index.json"

    def list_assets(self) -> list[AssetInfo]:
        return [AssetInfo(**entry) for entry in self._read_index()]

    def save_asset(self, kind: AssetKind, filename: str, content: bytes) -> AssetInfo:
        info = AssetInfo(
            id=uuid.uuid4().hex,
            kind=kind,
            filename=filename,
            uploaded_at=datetime.now(timezone.utc).isoformat(),
        )
        self.path_for(info.id).write_bytes(content)
        entries = self._read_index()
        entries.append(asdict(info))
        self._write_index(entries)
        return info

    def delete_asset(self, asset_id: str) -> None:
        entries = [e for e in self._read_index() if e["id"] != asset_id]
        self._write_index(entries)
        path = self.path_for(asset_id)
        if path.exists():
            path.unlink()

    def path_for(self, asset_id: str) -> Path:
        return self._data_dir / f"{asset_id}.wav"

    def _read_index(self) -> list[dict]:
        if not self._index_path.exists():
            return []
        return json.loads(self._index_path.read_text())

    def _write_index(self, entries: list[dict]) -> None:
        self._index_path.write_text(json.dumps(entries, indent=2))
