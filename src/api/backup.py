"""Complete backups: one zip archive with everything needed to rebuild the
repeater on a fresh install.

    manifest.json        what this is, when it was made, and what's inside
    settings.json        config, DTMF macros and announcements -- the same
                         JSON as the dashboard's settings-only download
    users.json           dashboard accounts (password hashes, not passwords)
    audio/               uploaded clips, with their index.json
    history/activity.db  airtime statistics
    history/audit.db     the audit log
    recordings/          saved transmissions, only when asked for (they add up)

Not included: the environment file (the built-in admin and the Asterisk
login) and Asterisk's own configuration, phone line included.

A restore checks the whole archive before changing anything. Recordings are
added to the ones already here; everything else is replaced.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import re
import shutil
import tempfile
import time
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Iterator, Optional, get_args

from .assets import ASSET_ID, AssetInfo, AssetKind
from .persistence import database_has_table
from .safe_paths import child_path

if TYPE_CHECKING:
    from controller.state_machine import RepeaterConfig

    from .activity import ActivityStore
    from .assets import AudioAssetStore
    from .audit import AuditLog
    from .recordings import RecordingStore
    from .service import RepeaterService
    from .users import UserStore

FORMAT = "moreopenrepeater-backup"
VERSION = 1
_RECORDING = re.compile(r"^recordings/(\d{13,})\.wav$")
_NAME = re.compile(r"^moreopenrepeater-[a-z0-9-]+-\d{8}-\d{6}\.zip$")
_HISTORY = {"activity": ("activity", "airtime statistics"), "audit": ("audit", "audit log")}

_logger = logging.getLogger("moreopenrepeater.backup")


class BackupError(ValueError):
    """The file isn't a backup this version can restore."""


@dataclass
class BackupSources:
    """Everything a backup reads from and a restore writes to."""

    service: RepeaterService
    users: UserStore
    assets: AudioAssetStore
    recordings: RecordingStore
    activity: ActivityStore
    audit: AuditLog


def backup_name(callsign: str, now: float) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", callsign.lower()).strip("-") or "repeater"
    return f"moreopenrepeater-{slug}-{time.strftime('%Y%m%d-%H%M%S', time.localtime(now))}.zip"


def write_backup(path: Path, sources: BackupSources, include_recordings: bool, now: float) -> dict:
    """Writes a backup archive to `path` and returns its manifest."""
    settings = sources.service.export_snapshot()
    users = sources.users.export()
    assets = [a for a in sources.assets.list_assets() if sources.assets.has(a.id)]
    recordings = sources.recordings.paths() if include_recordings else []
    added_recordings = 0
    with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("settings.json", json.dumps(settings, indent=2))
        archive.writestr("users.json", json.dumps(users, indent=2))
        archive.writestr("audio/index.json", json.dumps([dataclasses.asdict(a) for a in assets], indent=2))
        for asset in assets:
            archive.write(sources.assets.path_for(asset.id), f"audio/{asset.id}.wav", compress_type=zipfile.ZIP_STORED)
        for name, store in (("activity", sources.activity), ("audit", sources.audit)):
            copy = Path(tmp) / f"{name}.db"
            store.copy_to(copy)
            archive.write(copy, f"history/{name}.db")
        for source in recordings:
            try:
                archive.write(source, f"recordings/{source.name}", compress_type=zipfile.ZIP_STORED)
            except FileNotFoundError:  # pruned while we were writing
                continue
            added_recordings += 1
        manifest = {
            "format": FORMAT,
            "version": VERSION,
            "created_at": now,
            "callsign": settings["config"].get("callsign", ""),
            "contents": {
                "macros": len(settings["macros"]),
                "announcements": len(settings["announcements"]),
                "users": len(users["users"]),
                "audio_clips": len(assets),
                "recordings": added_recordings,
            },
        }
        archive.writestr("manifest.json", json.dumps(manifest, indent=2))
    return manifest


@dataclass
class OpenedBackup:
    """A checked archive, unpacked into a temporary directory."""

    manifest: dict
    settings: dict
    users: Optional[dict]
    assets: list[AssetInfo]
    history: dict[str, Path]  # "activity"/"audit" -> an intact database file
    recordings: list[Path]
    directory: Path


@contextmanager
def open_backup(path: Path) -> Iterator[OpenedBackup]:
    with tempfile.TemporaryDirectory() as tmp:
        yield _unpack(path, Path(tmp))


def _unpack(path: Path, directory: Path) -> OpenedBackup:
    try:
        archive = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError):
        raise BackupError("That isn't a moreopenrepeater backup (it's not a zip file).") from None
    with archive:
        names = set(archive.namelist())

        def extract(name: str, dest: Path) -> Path:
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                with archive.open(name) as src, dest.open("wb") as out:
                    shutil.copyfileobj(src, out)
            except (zipfile.BadZipFile, OSError) as error:
                raise BackupError(f"{name} in the backup is damaged ({error}).") from None
            return dest

        def read_json(name: str):
            if name not in names:
                raise BackupError(f"The backup is missing {name}.")
            try:
                return json.loads(extract(name, directory / name).read_text())
            except ValueError:
                raise BackupError(f"{name} in the backup is damaged.") from None

        manifest = read_json("manifest.json")
        if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
            raise BackupError("That isn't a moreopenrepeater backup.")
        if not isinstance(manifest.get("version"), int) or manifest["version"] > VERSION:
            raise BackupError("That backup is from a newer version of moreopenrepeater. Update this one first.")
        settings = read_json("settings.json")
        if not isinstance(settings, dict):
            raise BackupError("settings.json in the backup is damaged.")
        users = read_json("users.json") if "users.json" in names else None

        assets = []
        for entry in read_json("audio/index.json") if "audio/index.json" in names else []:
            try:
                asset = AssetInfo(**entry)
            except TypeError:
                raise BackupError("The audio clip list in the backup is damaged.") from None
            if not ASSET_ID.fullmatch(str(asset.id)) or asset.kind not in get_args(AssetKind):
                raise BackupError("The audio clip list in the backup is damaged.")
            member = f"audio/{asset.id}.wav"
            if member not in names:
                raise BackupError(f"The backup is missing the audio clip {asset.filename!r}.")
            extract(member, directory / member)
            assets.append(asset)

        history = {}
        for name, (table, label) in _HISTORY.items():
            member = f"history/{name}.db"
            if member in names:
                database = extract(member, directory / member)
                if not database_has_table(database, table):
                    raise BackupError(f"The {label} in the backup is damaged.")
                history[name] = database

        recordings = [
            extract(member, directory / member) for member in sorted(names) if _RECORDING.match(member)
        ]
    return OpenedBackup(manifest, settings, users, assets, history, recordings, directory)


def restore_backup(
    backup: OpenedBackup,
    sources: BackupSources,
    settings: dict,
    import_settings: Optional[Callable[[dict], None]] = None,
) -> dict:
    """Applies an opened backup. `settings` is `backup.settings` after the
    API's validation; users are checked here, before anything changes.
    `import_settings` defaults to `service.import_snapshot` (the API runs
    it on the event loop instead of this worker thread).
    A backup with no dashboard accounts leaves the current ones alone,
    rather than turning sign-in off."""
    users = sources.users.parse(backup.users) if backup.users is not None else None
    sources.assets.replace(backup.assets, backup.directory / "audio")
    (import_settings or sources.service.import_snapshot)(settings)
    if users:
        sources.users.replace(users)
    for name, path in backup.history.items():
        getattr(sources, name).replace_from(path)
    added = sum(sources.recordings.import_file(p) for p in backup.recordings) if sources.recordings.enabled else 0
    _logger.info("restored a backup from %s", time.ctime(backup.manifest.get("created_at", 0)))
    return {
        "macros": len(settings.get("macros", [])),
        "announcements": len(settings.get("announcements", [])),
        "users": len(users) if users else None,
        "audio_clips": len(backup.assets),
        "history": bool(backup.history),
        "recordings": added,
    }


@dataclass(frozen=True)
class SavedBackup:
    name: str
    created_at: float
    size: int
    contents: dict


class BackupFolder:
    """Backups kept on the repeater (or a mounted drive), made on demand or
    on a schedule. `directory=None` turns this off (tests)."""

    def __init__(self, directory: Optional[Path]) -> None:
        self.directory = directory
        self.last_error: Optional[str] = None

    @property
    def enabled(self) -> bool:
        return self.directory is not None

    def list(self) -> list[SavedBackup]:
        if self.directory is None or not self.directory.is_dir():
            return []
        saved = [self._describe(p) for p in self.directory.iterdir() if _NAME.match(p.name)]
        return sorted(saved, key=lambda b: b.created_at, reverse=True)

    def path_for(self, name: str) -> Path:
        if self.directory is None or not _NAME.match(name):
            raise KeyError(name)
        path = child_path(self.directory, name)
        if not path.is_file():
            raise KeyError(name)
        return path

    def create(self, sources: BackupSources, include_recordings: bool, now: float) -> SavedBackup:
        if self.directory is None:
            raise OSError("No backup folder is set up")
        self.directory.mkdir(parents=True, exist_ok=True)
        name = backup_name(sources.service.config.callsign, now)
        tmp = self.directory / f".{name}.tmp"
        try:
            write_backup(tmp, sources, include_recordings, now)
            tmp.replace(self.directory / name)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        return self._describe(self.directory / name)

    def delete(self, name: str) -> None:
        self.path_for(name).unlink()

    def prune(self, keep: int) -> int:
        old = self.list()[keep:]
        for backup in old:
            (self.directory / backup.name).unlink(missing_ok=True)
        return len(old)

    def run_schedule(self, sources: BackupSources, config: RepeaterConfig, now: float) -> Optional[SavedBackup]:
        """Makes a backup if one is due, then trims old ones. Errors (a full
        disk, an unplugged drive) are kept for the dashboard, not raised."""
        if self.directory is None or not config.backup_enabled:
            return None
        saved = self.list()
        if saved and now - saved[0].created_at < config.backup_interval_hours * 3600:
            return None
        try:
            backup = self.create(sources, config.backup_include_recordings, now)
            self.prune(config.backup_keep)
        except Exception as error:  # noqa: BLE001 -- shown on the dashboard; the next check retries
            self.last_error = f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(now))}: {error}"
            _logger.exception("scheduled backup failed")
            return None
        self.last_error = None
        _logger.info("scheduled backup saved to %s", self.directory / backup.name)
        return backup

    def _describe(self, path: Path) -> SavedBackup:
        stat = path.stat()
        manifest: dict = {}
        try:
            with zipfile.ZipFile(path) as archive:
                manifest = json.loads(archive.read("manifest.json"))
        except (zipfile.BadZipFile, KeyError, ValueError, OSError):
            pass
        created_at = manifest.get("created_at") if isinstance(manifest.get("created_at"), (int, float)) else stat.st_mtime
        contents = manifest.get("contents") if isinstance(manifest.get("contents"), dict) else {}
        return SavedBackup(path.name, created_at, stat.st_size, contents)
