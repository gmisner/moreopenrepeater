"""FastAPI app: REST + WebSocket status API, plus the static dashboard."""
from __future__ import annotations

import asyncio
import dataclasses
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from audio_io.serial_lines import find_serial_ports
from controller.state_machine import RepeaterConfig
from playout.renderer import ClipRenderer
from wx.nws import fetch_active_alerts, fetch_zone_geometry

from .activity import ActivityStore
from .agents import AgentAccess
from .airport_weather import AirportWeather
from .allstar_node import AllStarNode
from .aprs_map import StationStore
from .assets import AudioAssetStore
from .audit import AuditEntry, AuditLog
from .auth import AuthSettings
from .autopatch import Autopatch
from .backup import BackupError, BackupFolder, open_backup, restore_backup
from .boards import run_amixer
from .control_codes import ControlCodes
from .gpio import GpioControl
from .health import RunMarker, SystemProbe
from .homeassistant import HomeAssistant
from .link_radio import LinkRadio
from .links import LinkControl
from .live_audio import LiveAudio, list_audio_devices
from .log_hub import LogHub, LogShipper
from .mailbox import MailboxStore
from .models import AuditEntryResponse, ConfigResponse, SessionResponse, SnapshotImportRequest, StatusResponse
from .monitor_receiver import MonitorReceiverService
from .node_directory import NodeDirectory
from .notify import Notifier
from .persistence import StateStore
from .recordings import RecordingStore
from .remote_base import RemoteBase
from .routes import accounts as accounts_routes
from .routes import activity as activity_routes
from .routes import admin_ops as admin_ops_routes
from .routes import audio_tools as audio_tools_routes
from .routes import autopatch as autopatch_routes
from .routes import control_codes as control_codes_routes
from .routes import core as core_routes
from .routes import linking as linking_routes
from .routes import mailbox as mailbox_routes
from .routes import media as media_routes
from .routes import nets as nets_routes
from .routes import programming as programming_routes
from .routes import realtime as realtime_routes
from .routes import session as session_routes
from .routes import simulate as simulate_routes
from .routes import site as site_routes
from .runtime import (
    DEFAULT_ACTIVITY_PATH,
    DEFAULT_AGENT_ACCESS_PATH,
    DEFAULT_ALERTS_PATH,
    DEFAULT_APRS_PATH,
    DEFAULT_AUDIT_PATH,
    DEFAULT_AUTO_UPDATE_PATH,
    DEFAULT_BACKUP_DIR,
    DEFAULT_CONTROL_CODES_PATH,
    DEFAULT_LOG_SHIPPING_PATH,
    DEFAULT_MAILBOX_DIR,
    DEFAULT_MONITOR_RECORDINGS_DIR,
    DEFAULT_NETS_PATH,
    DEFAULT_NODE_LIST_PATH,
    DEFAULT_RECORDINGS_DIR,
    DEFAULT_RUN_MARKER_PATH,
    DEFAULT_STATE_PATH,
    DEFAULT_STREAM_PATH,
    DEFAULT_TOKENS_PATH,
    DEFAULT_USERS_PATH,
    LOGIN_FAILURE_DELAY_SECONDS,
    REPO_DIR,
    SAFE_METHODS,
    WEB_DIR,
    AppRuntime,
    Identity,
    LinkSettings,
    _LOCAL,
    _UNAUDITED_PATHS,
    _resolve_data_dir,
    _resolve_log_path,
    aprs_beacon_packet,
    link_settings_from_env,
)
from .service import RepeaterService, StatusSnapshot
from .sip_trunk import SipTrunk
from .stream import Streamer
from .tokens import TokenStore
from .transcripts import Transcriber
from .tx_fan import TxFan
from .tailscale import TailscaleControl
from .updates import UpdateChecker, Updater
from .users import UserError, UserStore
from .watchdog import Watchdog

# Re-exports used by tests and callers that historically imported from api.app.
__all__ = [
    "AppRuntime",
    "Identity",
    "LinkSettings",
    "aprs_beacon_packet",
    "create_app",
    "link_settings_from_env",
    "_resolve_data_dir",
    "_resolve_log_path",
]


def _status_response(snapshot: StatusSnapshot) -> StatusResponse:
    return StatusResponse(**dataclasses.asdict(snapshot))


def _config_response(service: RepeaterService) -> ConfigResponse:
    return ConfigResponse(**dataclasses.asdict(service.saved_config))


def _audit_response(entry: AuditEntry) -> AuditEntryResponse:
    return AuditEntryResponse(
        at=datetime.fromtimestamp(entry.at), actor=entry.actor, action=entry.action, detail=entry.detail, status=entry.status
    )


def _validated_config(values: dict) -> RepeaterConfig:
    """Type-check a full config dict through the API model, turning bad
    values into the same 422 FastAPI gives for a bad request body."""
    try:
        validated = ConfigResponse.model_validate({k: v for k, v in values.items() if k in ConfigResponse.model_fields})
    except ValidationError as error:
        raise RequestValidationError(error.errors()) from error
    return RepeaterConfig(**validated.model_dump())


def _snapshot_to_import(snapshot: SnapshotImportRequest) -> dict:
    config = _validated_config({**dataclasses.asdict(RepeaterConfig()), **snapshot.config})
    return {
        "config": dataclasses.asdict(config),
        "macros": [m.model_dump() for m in snapshot.macros],
        "announcements": [a.model_dump() for a in snapshot.announcements],
    }


def _apply_overrides(base: RepeaterConfig, overrides: dict) -> RepeaterConfig:
    """Layer dashboard form values (including `clear_<field>` flags) over a config."""
    merged = dataclasses.asdict(base)
    for key, value in overrides.items():
        if key.startswith("clear_"):
            if value:
                merged[key.removeprefix("clear_")] = None
        else:
            merged[key] = value
    return _validated_config(merged)


def create_app(
    service: Optional[RepeaterService] = None,
    start_background_tick: bool = True,
    assets_store: Optional[AudioAssetStore] = None,
    log_path: Optional[Path] = None,
    link_settings: Optional[LinkSettings] = None,
    auth_settings: Optional[AuthSettings] = None,
    state_store: Optional[StateStore] = None,
    renderer: Optional[ClipRenderer] = None,
    fetch_weather: Callable[..., dict] = fetch_active_alerts,
    fetch_zone: Callable[..., Optional[dict]] = fetch_zone_geometry,
    activity_store: Optional[ActivityStore] = None,
    live_audio: Optional[LiveAudio] = None,
    audio_devices: Callable[[], list[dict]] = list_audio_devices,
    recordings: Optional[RecordingStore] = None,
    users: Optional[UserStore] = None,
    tokens: Optional[TokenStore] = None,
    agent_access: Optional[AgentAccess] = None,
    audit: Optional[AuditLog] = None,
    aprs_stations: Optional[StationStore] = None,
    autopatch: Optional[Autopatch] = None,
    sip_trunk: Optional[SipTrunk] = None,
    backups: Optional[BackupFolder] = None,
    allstar_node: Optional[AllStarNode] = None,
    gpio: Optional[GpioControl] = None,
    tx_fan: Optional[TxFan] = None,
    node_directory: Optional[NodeDirectory] = None,
    links: Optional[LinkControl] = None,
    updater: Optional[Updater] = None,
    update_checker: Optional[UpdateChecker] = None,
    update_channel: Optional[str] = None,
    tailscale: Optional[TailscaleControl] = None,
    notifier: Optional[Notifier] = None,
    log_hub: Optional[LogHub] = None,
    log_shipper: Optional[LogShipper] = None,
    run_marker: Optional[RunMarker] = None,
    system_probe: Optional[SystemProbe] = None,
    watchdog: Optional[Watchdog] = None,
    net_store: Optional[StateStore] = None,
    auto_update_store: Optional[StateStore] = None,
    control_codes: Optional[ControlCodes] = None,
    homeassistant: Optional[HomeAssistant] = None,
    airport_weather: Optional[AirportWeather] = None,
    stream_store: Optional[StateStore] = None,
    streamer: Optional[Streamer] = None,
    mailbox_store: Optional[MailboxStore] = None,
    transcriber: Optional[Transcriber] = None,
    monitor_recordings: Optional[RecordingStore] = None,
    monitor_receiver: Optional[MonitorReceiverService] = None,
    link_radio: Optional[LinkRadio] = None,
    remote_base: Optional[RemoteBase] = None,
    run_mixer: Callable[[list[str]], object] = run_amixer,
    serial_ports: Callable[[], list[dict]] = find_serial_ports,
) -> FastAPI:
    """`state_store`, `activity_store` and `recordings` default to in-memory
    (or off) so tests never touch the real files under `data/`; the
    module-level `app` below opts in.
    `fetch_weather(lat, lon, contact)` and `fetch_zone(url, contact)` are swappable so tests stay offline."""
    runtime = AppRuntime(
        service=service,
        start_background_tick=start_background_tick,
        assets_store=assets_store,
        log_path=log_path,
        link_settings=link_settings,
        auth_settings=auth_settings,
        state_store=state_store,
        renderer=renderer,
        fetch_weather=fetch_weather,
        fetch_zone=fetch_zone,
        activity_store=activity_store,
        live_audio=live_audio,
        audio_devices=audio_devices,
        recordings=recordings,
        users=users,
        tokens=tokens,
        agent_access=agent_access,
        audit=audit,
        aprs_stations=aprs_stations,
        autopatch=autopatch,
        sip_trunk=sip_trunk,
        backups=backups,
        allstar_node=allstar_node,
        gpio=gpio,
        tx_fan=tx_fan,
        node_directory=node_directory,
        links=links,
        updater=updater,
        update_checker=update_checker,
        update_channel=update_channel,
        tailscale=tailscale,
        notifier=notifier,
        log_hub=log_hub,
        log_shipper=log_shipper,
        run_marker=run_marker,
        system_probe=system_probe,
        watchdog=watchdog,
        net_store=net_store,
        auto_update_store=auto_update_store,
        control_codes=control_codes,
        homeassistant=homeassistant,
        airport_weather=airport_weather,
        stream_store=stream_store,
        streamer=streamer,
        mailbox_store=mailbox_store,
        transcriber=transcriber,
        monitor_recordings=monitor_recordings,
        monitor_receiver=monitor_receiver,
        link_radio=link_radio,
        remote_base=remote_base,
        run_mixer=run_mixer,
        serial_ports=serial_ports,
    )
    app = FastAPI(title="moreopenrepeater API", lifespan=runtime.lifespan)
    app.state.service = runtime.service
    agent_endpoint = runtime.attach_agent(app)

    @app.middleware("http")
    async def revalidate_static_files(request: Request, call_next):
        # Without this, browsers heuristically cache hashed SPA assets and
        # keep running the old dashboard after an upgrade. "no-cache" still
        # allows caching -- it just forces a cheap ETag revalidation (304).
        response = await call_next(request)
        path = request.url.path
        if not path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-cache")
        elif request.method not in SAFE_METHODS and path not in _UNAUDITED_PATHS:
            state = request.state
            identity = getattr(state, "identity", None)
            actor = getattr(state, "audit_actor", None) or (identity and identity.actor)
            runtime.audit.record(
                time.time(),
                actor or ("local" if not runtime.auth_enabled() else "anonymous"),
                f"{request.method} {path}",
                getattr(state, "audit_detail", ""),
                response.status_code,
            )
        return response

    def session_response(identity: Optional[Identity]) -> SessionResponse:
        return SessionResponse(
            auth_required=runtime.auth_enabled(),
            authenticated=identity is not None,
            username=identity.username if identity else None,
            role=identity.role if identity else None,
        )

    session_routes.register(
        app,
        identify=runtime.identify,
        auth_enabled=runtime.auth_enabled,
        check_login=runtime.check_login,
        sessions=runtime.sessions,
        session_response=session_response,
        local_identity=_LOCAL,
        login_failure_delay=LOGIN_FAILURE_DELAY_SECONDS,
    )

    core_routes.register(
        app,
        service=runtime.service,
        run_mixer=runtime.run_mixer,
        render_and_queue=runtime.render_and_queue,
        status_response=_status_response,
        config_response=_config_response,
        auth_dependencies=runtime.auth_dependencies,
        transmit_dependencies=runtime.transmit_dependencies,
    )

    simulate_routes.register(
        app,
        service=runtime.service,
        transmit_dependencies=runtime.transmit_dependencies,
        status_response=_status_response,
    )

    def actor_of(request: Request) -> str:
        return request.state.identity.username or "local"

    nets_routes.register(
        app,
        nets=runtime.nets,
        actor_of=actor_of,
        auth_dependencies=runtime.auth_dependencies,
        transmit_dependencies=runtime.transmit_dependencies,
    )

    programming_routes.register(
        app,
        service=runtime.service,
        assets_store=runtime.assets_store,
        zone_shapes=runtime.zone_shapes,
        render_and_queue=runtime.render_and_queue,
        check_weather=runtime.check_weather,
        status_response=_status_response,
        auth_dependencies=runtime.auth_dependencies,
        transmit_dependencies=runtime.transmit_dependencies,
    )

    activity_routes.register(
        app,
        service=runtime.service,
        activity_store=runtime.activity_store,
        snapshot_to_import=_snapshot_to_import,
        auth_dependencies=runtime.auth_dependencies,
    )

    def restore_backup_file(path: Path, loop: asyncio.AbstractEventLoop) -> dict:
        """Runs in a worker thread; the settings themselves are applied on the event loop."""

        async def import_on_loop(settings: dict) -> None:
            runtime.service.import_snapshot(settings)

        def import_settings(settings: dict) -> None:
            asyncio.run_coroutine_threadsafe(import_on_loop(settings), loop).result()

        try:
            with open_backup(path) as backup:
                try:
                    settings = _snapshot_to_import(SnapshotImportRequest.model_validate(backup.settings))
                except (ValidationError, RequestValidationError):
                    raise BackupError("The settings in the backup aren't valid for this version.") from None
                return restore_backup(backup, runtime.backup_sources, settings, import_settings)
        except (BackupError, UserError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from None

    admin_ops_routes.register(
        app,
        service=runtime.service,
        backups=runtime.backups,
        backup_sources=runtime.backup_sources,
        audit=runtime.audit,
        updater=runtime.updater,
        update_checker=runtime.update_checker,
        update_channel=runtime.update_channel,
        auto_updater=runtime.auto_updater,
        notifier=runtime.notifier,
        health=runtime.health,
        live_audio=runtime.live_audio,
        log_path=runtime.log_path,
        log_shipper=runtime.log_shipper,
        restore_backup_file=restore_backup_file,
        repo_dir=REPO_DIR,
        admin_dependencies=runtime.admin_dependencies,
        tailscale=runtime.tailscale,
    )

    audio_tools_routes.register(
        app,
        service=runtime.service,
        renderer=runtime.renderer,
        live_audio=runtime.live_audio,
        audio_devices=runtime.audio_devices,
        serial_ports=runtime.serial_ports,
        apply_overrides=_apply_overrides,
        auth_dependencies=runtime.auth_dependencies,
    )

    linking_routes.register(
        app,
        service=runtime.service,
        autopatch=runtime.autopatch,
        allstar_node=runtime.allstar_node,
        links=runtime.links,
        link_scheduler=runtime.link_scheduler,
        gpio=runtime.gpio,
        tx_fan=runtime.tx_fan,
        monitor_receiver=runtime.monitor_receiver,
        link_radio=runtime.link_radio,
        remote_base=runtime.remote_base,
        auth_dependencies=runtime.auth_dependencies,
        admin_dependencies=runtime.admin_dependencies,
        transmit_dependencies=runtime.transmit_dependencies,
    )

    autopatch_routes.register(
        app,
        service=runtime.service,
        autopatch=runtime.autopatch,
        sip_trunk=runtime.sip_trunk,
        auth_dependencies=runtime.auth_dependencies,
        admin_dependencies=runtime.admin_dependencies,
        transmit_dependencies=runtime.transmit_dependencies,
    )

    media_routes.register(
        app,
        service=runtime.service,
        assets_store=runtime.assets_store,
        recordings=runtime.recordings,
        monitor_recordings=runtime.monitor_recordings,
        transcriber=runtime.transcriber,
        auth_dependencies=runtime.auth_dependencies,
    )

    mailbox_routes.register(
        app,
        mailbox_store=runtime.mailbox_store,
        admin_dependencies=runtime.admin_dependencies,
    )

    site_routes.register(
        app,
        service=runtime.service,
        log_path=runtime.log_path,
        log_hub=runtime.log_hub,
        aprs_stations=runtime.aprs_stations,
        aprs_receiver=runtime.aprs_receiver,
        aprs_window_start=runtime.aprs_window_start,
        homeassistant=runtime.homeassistant,
        airport_weather=runtime.airport_weather,
        streamer=runtime.streamer,
        audit=runtime.audit,
        audit_response=_audit_response,
        actor_of=actor_of,
        auth_dependencies=runtime.auth_dependencies,
        admin_dependencies=runtime.admin_dependencies,
    )

    accounts_routes.register(
        app,
        users=runtime.users,
        tokens=runtime.tokens,
        agent_access=runtime.agent_access,
        agent_endpoint=agent_endpoint,
        auth_settings=runtime.auth_settings,
        control_codes=runtime.control_codes,
        role_of=runtime.role_of,
        account_dependencies=runtime.account_dependencies,
    )

    control_codes_routes.register(
        app,
        service=runtime.service,
        control_codes=runtime.control_codes,
        auth_dependencies=runtime.auth_dependencies,
        admin_dependencies=runtime.admin_dependencies,
    )

    realtime_routes.register(
        app,
        service=runtime.service,
        nets=runtime.nets,
        live_audio=runtime.live_audio,
        monitor_receiver=runtime.monitor_receiver,
        renderer=runtime.renderer,
        public_listeners=runtime.public_listeners,
        identify=runtime.identify,
        auth_enabled=runtime.auth_enabled,
        status_response=_status_response,
        web_dir=WEB_DIR,
    )

    return app



_LOG_HUB = LogHub()
app = create_app(
    state_store=StateStore(DEFAULT_STATE_PATH),
    activity_store=ActivityStore(DEFAULT_ACTIVITY_PATH),
    recordings=RecordingStore(DEFAULT_RECORDINGS_DIR),
    monitor_recordings=RecordingStore(DEFAULT_MONITOR_RECORDINGS_DIR),
    mailbox_store=MailboxStore(DEFAULT_MAILBOX_DIR, StateStore(DEFAULT_MAILBOX_DIR / "boxes.json")),
    users=UserStore(StateStore(DEFAULT_USERS_PATH)),
    tokens=TokenStore(StateStore(DEFAULT_TOKENS_PATH)),
    agent_access=AgentAccess(StateStore(DEFAULT_AGENT_ACCESS_PATH)),
    audit=AuditLog(DEFAULT_AUDIT_PATH),
    aprs_stations=StationStore(DEFAULT_APRS_PATH),
    backups=BackupFolder(DEFAULT_BACKUP_DIR),
    node_directory=NodeDirectory(DEFAULT_NODE_LIST_PATH),
    notifier=Notifier(StateStore(DEFAULT_ALERTS_PATH)),
    log_hub=_LOG_HUB,
    log_shipper=LogShipper(_LOG_HUB, StateStore(DEFAULT_LOG_SHIPPING_PATH)),
    run_marker=RunMarker(StateStore(DEFAULT_RUN_MARKER_PATH)),
    net_store=StateStore(DEFAULT_NETS_PATH),
    auto_update_store=StateStore(DEFAULT_AUTO_UPDATE_PATH),
    control_codes=ControlCodes(StateStore(DEFAULT_CONTROL_CODES_PATH)),
    stream_store=StateStore(DEFAULT_STREAM_PATH),
)


def main() -> None:
    import uvicorn

    host = os.environ.get("MOREOPENREPEATER_HOST", "0.0.0.0")
    port = int(os.environ.get("MOREOPENREPEATER_PORT", "8000"))
    uvicorn.run("api.app:app", host=host, port=port)


if __name__ == "__main__":
    main()
