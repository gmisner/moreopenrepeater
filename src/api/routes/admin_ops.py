"""Backups, software updates, and outbound alert settings."""
from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Sequence

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from audio_io.cm108 import find_interfaces
from audio_io.serial_lines import find_serial_ports

from ..audit import AuditLog
from ..backup import BackupFolder, BackupSources, backup_name, write_backup
from ..health import HealthMonitor
from ..live_audio import LiveAudio, list_audio_devices
from ..models import (
    AlertSettingsRequest,
    AlertSettingsResponse,
    AlertTestResponse,
    AlertsResponse,
    AutoUpdateSettings,
    BackupFolderResponse,
    BackupRestoreResponse,
    SavedBackupResponse,
    UpdateChannel,
    UpdateCheckResponse,
    UpdateRequest,
    UpdatesResponse,
)
from ..notify import SECRET_FIELDS, Notifier
from ..service import RepeaterService
from ..auto_update import AutoUpdater
from ..support_bundle import support_bundle_name, write_support_bundle
from ..updates import UpdateChecker, Updater, installed_version, read_log


def register(
    app: FastAPI,
    *,
    service: RepeaterService,
    backups: BackupFolder,
    backup_sources: BackupSources,
    audit: AuditLog,
    updater: Updater,
    update_checker: UpdateChecker,
    update_channel: str,
    auto_updater: AutoUpdater,
    notifier: Notifier,
    health: HealthMonitor,
    live_audio: LiveAudio,
    log_path: Path,
    restore_backup_file: Callable,
    repo_dir: Path,
    admin_dependencies: Sequence,
) -> None:
    @app.get("/api/backup", dependencies=admin_dependencies)
    def download_backup(request: Request, recordings: bool = False) -> FileResponse:
        now = time.time()
        fd, name = tempfile.mkstemp(suffix=".zip")
        os.close(fd)
        path = Path(name)
        try:
            write_backup(path, backup_sources, recordings, now)
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        # A GET, so the middleware skips it -- but it hands out password hashes.
        audit.record(now, request.state.identity.actor or "local", "download backup", "with recordings" if recordings else "")
        return FileResponse(
            path,
            media_type="application/zip",
            filename=backup_name(service.config.callsign, now),
            background=BackgroundTask(path.unlink, missing_ok=True),
        )

    @app.get("/api/support-bundle", dependencies=admin_dependencies)
    def download_support_bundle(request: Request) -> FileResponse:
        now = time.time()
        fd, name = tempfile.mkstemp(suffix=".zip")
        os.close(fd)
        path = Path(name)
        try:
            write_support_bundle(
                path,
                service=service,
                log_path=log_path,
                repo_dir=repo_dir,
                update_channel=update_channel,
                live_audio_status=live_audio.status(),
                system_readings=health.probe.readings(),
                updater=updater,
                now=now,
                receiver_openings=list(reversed(live_audio.openings)),
                audio_glitches=list(reversed(live_audio.glitches)),
                last_unexpected_stop=health.last_unexpected_stop,
                audio_devices=list_audio_devices(),
                serial_ports=find_serial_ports(),
                cm108_interfaces=find_interfaces(),
                cm108_path=getattr(getattr(live_audio.cm108, "device", None), "path", None),
            )
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        audit.record(now, request.state.identity.actor or "local", "download support bundle", "")
        return FileResponse(
            path,
            media_type="application/zip",
            filename=support_bundle_name(service.config.callsign, now),
            background=BackgroundTask(path.unlink, missing_ok=True),
        )

    @app.post("/api/backup/restore", response_model=BackupRestoreResponse, dependencies=admin_dependencies)
    async def restore_uploaded_backup(request: Request, file: UploadFile = File(...)) -> dict:
        request.state.audit_detail = file.filename or ""
        loop = asyncio.get_running_loop()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "backup.zip"
            with path.open("wb") as out:
                await loop.run_in_executor(None, shutil.copyfileobj, file.file, out)
            return await loop.run_in_executor(None, restore_backup_file, path, loop)

    def backup_folder_response() -> BackupFolderResponse:
        return BackupFolderResponse(
            enabled=backups.enabled,
            directory=str(backups.directory) if backups.directory else None,
            last_error=backups.last_error,
            backups=[
                SavedBackupResponse(
                    name=b.name, created_at=datetime.fromtimestamp(b.created_at), size=b.size, contents=b.contents
                )
                for b in backups.list()
            ],
        )

    def saved_backup_path(name: str) -> Path:
        try:
            return backups.path_for(name)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"No backup named {name!r}") from None

    @app.get("/api/backups", response_model=BackupFolderResponse, dependencies=admin_dependencies)
    def list_backups() -> BackupFolderResponse:
        return backup_folder_response()

    @app.post("/api/backups", response_model=BackupFolderResponse, dependencies=admin_dependencies)
    async def create_saved_backup() -> BackupFolderResponse:
        if not backups.enabled:
            raise HTTPException(status_code=409, detail="No backup folder is set up")
        config = service.config
        try:
            await asyncio.get_running_loop().run_in_executor(
                None, backups.create, backup_sources, config.backup_include_recordings, time.time()
            )
        except OSError as error:
            raise HTTPException(status_code=507, detail=f"Couldn't save the backup: {error}") from None
        return backup_folder_response()

    @app.get("/api/backups/{name}", dependencies=admin_dependencies)
    def download_saved_backup(name: str, request: Request) -> FileResponse:
        path = saved_backup_path(name)
        audit.record(time.time(), request.state.identity.actor or "local", "download backup", name)
        return FileResponse(path, media_type="application/zip", filename=name)

    @app.delete("/api/backups/{name}", response_model=BackupFolderResponse, dependencies=admin_dependencies)
    def delete_saved_backup(name: str) -> BackupFolderResponse:
        saved_backup_path(name).unlink(missing_ok=True)
        return backup_folder_response()

    @app.post("/api/backups/{name}/restore", response_model=BackupRestoreResponse, dependencies=admin_dependencies)
    async def restore_saved_backup(name: str, request: Request) -> dict:
        request.state.audit_detail = name
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, restore_backup_file, saved_backup_path(name), loop)

    @app.get("/api/updates", response_model=UpdatesResponse, dependencies=admin_dependencies)
    def get_updates() -> dict:
        return {
            "available": updater.available,
            "channel": update_channel,
            "version": installed_version(repo_dir),
            "status": updater.status(),
            "auto": auto_updater.status(),
        }

    @app.put("/api/updates/auto", response_model=UpdatesResponse, dependencies=admin_dependencies)
    def set_auto_update(body: AutoUpdateSettings, request: Request) -> dict:
        request.state.audit_detail = f"{body.start}-{body.end}" if body.enabled else "off"
        auto_updater.update_settings(**body.model_dump())
        return get_updates()

    @app.get("/api/updates/check", response_model=UpdateCheckResponse, dependencies=admin_dependencies)
    def check_for_update(channel: UpdateChannel, refresh: bool = False) -> dict:
        version = installed_version(repo_dir)
        return update_checker.check(channel, version["sha"] if version else "", refresh=refresh)

    @app.get("/api/updates/log", response_model=list[str], dependencies=admin_dependencies)
    def get_update_log() -> list[str]:
        return read_log(updater.settings) if updater.settings else []

    @app.post("/api/updates", response_model=UpdatesResponse, dependencies=admin_dependencies)
    def start_update(body: UpdateRequest, request: Request) -> dict:
        request.state.audit_detail = body.channel
        if not updater.available:
            raise HTTPException(status_code=409, detail="Updates are only available on installs made with install-pi.sh")
        if updater.busy():
            raise HTTPException(status_code=409, detail="An update is already in progress")
        try:
            updater.request(body.channel)
        except OSError as error:
            raise HTTPException(status_code=500, detail=f"Couldn't ask the updater: {error.strerror or error}") from None
        return get_updates()

    def alerts_response() -> dict:
        settings = notifier.settings
        return {
            "settings": AlertSettingsResponse(**{k: getattr(settings, k) for k in AlertSettingsResponse.model_fields}),
            "secrets_set": {name: bool(getattr(settings, name)) for name in SECRET_FIELDS},
            "channels": notifier.channels,
            "recent": [{**entry, "at": datetime.fromtimestamp(entry["at"])} for entry in notifier.recent],
            "system": health.probe.readings(),
            "last_unexpected_stop": health.last_unexpected_stop,
        }

    # Plain `def`s: the system readings read files and may run vcgencmd.
    @app.get("/api/alerts", response_model=AlertsResponse, dependencies=admin_dependencies)
    def get_alerts() -> dict:
        return alerts_response()

    @app.put("/api/alerts", response_model=AlertsResponse, dependencies=admin_dependencies)
    def put_alerts(body: AlertSettingsRequest, request: Request) -> dict:
        changes = body.model_dump(exclude_none=True)
        request.state.audit_detail = ", ".join(
            name for name, value in changes.items() if value != getattr(notifier.settings, name)
        )
        notifier.update(**changes)
        return alerts_response()

    @app.post("/api/alerts/test", response_model=AlertTestResponse, dependencies=admin_dependencies)
    async def test_alerts() -> dict:
        if not notifier.channels:
            raise HTTPException(status_code=409, detail="Set up at least one way to send alerts first")
        return {"results": await notifier.send_test()}
