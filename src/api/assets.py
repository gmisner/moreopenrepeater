"""Filesystem-backed storage for uploaded audio clips (courtesy tones, IDs, etc.).

No database -- assets live under a data directory as `<id>.wav` with a JSON
sidecar index for metadata, consistent with the project's general avoidance
of a database for state this small and low-write-volume.

This only manages storage; `playout.renderer.ClipRenderer` turns a selected
asset into transmit audio.
"""
from __future__ import annotations

import json
import re
import shutil
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from .safe_paths import child_path

AssetKind = Literal["courtesy_tone", "id", "timeout_tone", "custom"]
ASSET_ID = re.compile(r"[0-9a-f]{32}")  # uuid4().hex


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
        if self.has(asset_id):
            self.path_for(asset_id).unlink()

    def has(self, asset_id: str) -> bool:
        try:
            return self.path_for(asset_id).exists()
        except KeyError:
            return False

    def path_for(self, asset_id: str) -> Path:
        """KeyError for anything that isn't an asset ID."""
        if not ASSET_ID.fullmatch(asset_id):
            raise KeyError(asset_id)
        return child_path(self._data_dir, f"{asset_id}.wav")

    def replace(self, assets: list[AssetInfo], source_dir: Path) -> None:
        """Swap in another set of clips (e.g. from a backup), whose files are
        `<id>.wav` in `source_dir`. Files come first, so the index never
        points at a missing clip."""
        for asset in assets:
            shutil.copyfile(source_dir / f"{asset.id}.wav", self.path_for(asset.id))
        self._write_index([asdict(a) for a in assets])
        keep = {a.id for a in assets}
        for path in self._data_dir.glob("*.wav"):
            if path.stem not in keep:
                path.unlink(missing_ok=True)

    def _read_index(self) -> list[dict]:
        if not self._index_path.exists():
            return []
        return json.loads(self._index_path.read_text())

    def _write_index(self, entries: list[dict]) -> None:
        self._index_path.write_text(json.dumps(entries, indent=2))
