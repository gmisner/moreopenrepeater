from __future__ import annotations

from dataclasses import dataclass
"""BackupConfig: config facet (flat wire field names)."""


@dataclass(frozen=True)
class BackupConfig:
    # scheduled backups to the backup folder
    backup_enabled: bool = False  # scheduled backups to the backup folder
    backup_interval_hours: float = 24.0
    backup_keep: int = 7
    backup_include_recordings: bool = False
