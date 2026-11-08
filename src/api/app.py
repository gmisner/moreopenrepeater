"""FastAPI app: REST + WebSocket status API, plus the static dashboard."""
from __future__ import annotations

import asyncio
import dataclasses
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from controller.state_machine import RepeaterConfig

from .activity import ActivityStore
from .agents import AgentAccess
from .aprs_map import StationStore
from .audit import AuditEntry, AuditLog
from .backup import BackupError, BackupFolder, open_backup, restore_backup
from .control_codes import ControlCodes
from .health import RunMarker
from .hub import HubSiteStore, hub_sites_state_store
from .log_hub import LogHub, LogShipper
from .mailbox import MailboxStore
from .models import AuditEntryResponse, ConfigResponse, SessionResponse, SnapshotImportRequest, StatusResponse
from .node_directory import NodeDirectory
from .notify import Notifier
from .persistence import StateStore
from .recordings import RecordingStore
from .routes import accounts as accounts_routes
from .routes import activity as activity_routes
from .routes import admin_ops as admin_ops_routes
from .routes import audio_tools as audio_tools_routes
from .routes import autopatch as autopatch_routes
from .routes import control_codes as control_codes_routes
from .routes import core as core_routes
from .routes import hub as hub_routes
from .routes import linking as linking_routes
from .routes import mailbox as mailbox_routes
from .routes import media as media_routes
from .routes import metrics as metrics_routes
from .routes import nets as nets_routes
from .routes import programming as programming_routes
from .routes import realtime as realtime_routes
from .routes import sensors as sensors_routes
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
    DEFAULT_HUB_SITES_PATH,
    DEFAULT_LOG_SHIPPING_PATH,
    DEFAULT_MAILBOX_DIR,
    DEFAULT_MONITOR_RECORDINGS_DIR,
    DEFAULT_NETS_PATH,
    DEFAULT_NODE_LIST_PATH,
    DEFAULT_RECORDINGS_DIR,
    DEFAULT_RUN_MARKER_PATH,
    DEFAULT_SENSORS_PATH,
    DEFAULT_STATE_PATH,
    DEFAULT_STREAM_PATH,
    DEFAULT_TOKENS_PATH,
    DEFAULT_USERS_PATH,
    LOGIN_FAILURE_DELAY_SECONDS,
    REPO_DIR,
    SAFE_METHODS,
    WEB_DIR,
    AdminOpsBundle,
    AppRuntime,
    AuthBundle,
    CoreBundle,
    Identity,
    LinkSettings,
    LinkingBundle,
    MediaBundle,
    RuntimeBuilder,
    SiteOpsBundle,
    _LOCAL,
    _UNAUDITED_PATHS,
    _resolve_data_dir,
    _resolve_log_path,
    aprs_beacon_packet,
    bundles_from_overrides,
    link_settings_from_env,
)
from .service import RepeaterService, StatusSnapshot
from .site_sensors import SensorHistoryStore
from .tokens import TokenStore
from .users import UserError, UserStore

# Re-exports used by tests and callers that historically imported from api.app.
__all__ = [
    "AdminOpsBundle",
    "AppRuntime",
    "AuthBundle",
    "CoreBundle",
    "Identity",
    "LinkSettings",
    "LinkingBundle",
    "MediaBundle",
    "RuntimeBuilder",
    "SiteOpsBundle",
    "aprs_beacon_packet",
    "create_app",
    "link_settings_from_env",
    "_resolve_data_dir",
    "_resolve_log_path",
]


def _status_response(snapshot: StatusSnapshot) -> StatusResponse:
    return StatusResponse(**dataclasses.asdict(snapshot))


def _config_response(service: RepeaterService) -> ConfigResponse:
    return ConfigResponse.model_validate(service.saved_config.to_flat_dict())


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
    return RepeaterConfig.from_flat_dict(validated.model_dump())


def _snapshot_to_import(snapshot: SnapshotImportRequest) -> dict:
    config = _validated_config({**RepeaterConfig().to_flat_dict(), **snapshot.config})
    return {
        "config": config.to_flat_dict(),
        "macros": [m.model_dump() for m in snapshot.macros],
        "announcements": [a.model_dump() for a in snapshot.announcements],
    }


def _apply_overrides(base: RepeaterConfig, overrides: dict) -> RepeaterConfig:
    """Layer dashboard form values (including `clear_<field>` flags) over a config."""
    merged = base.to_flat_dict()
    for key, value in overrides.items():
        if key.startswith("clear_"):
            if value:
                merged[key.removeprefix("clear_")] = None
        else:
            merged[key] = value
    return _validated_config(merged)


def create_app(
    runtime: Optional[AppRuntime | RuntimeBuilder] = None,
    *,
    core: Optional[CoreBundle] = None,
    auth: Optional[AuthBundle] = None,
    linking: Optional[LinkingBundle] = None,
    media: Optional[MediaBundle] = None,
    site_ops: Optional[SiteOpsBundle] = None,
    admin: Optional[AdminOpsBundle] = None,
    **overrides: object,
) -> FastAPI:
    """Compose the FastAPI shell around an ``AppRuntime``.

    Prefer ``create_app(runtime=...)`` or typed bags (``auth=AuthBundle(...)``, …).
    Flat ``**overrides`` are packed by ``RuntimeBuilder`` so tests keep a short
    call site without duplicating the wide param list on both surfaces.
    """
    bags = (core, auth, linking, media, site_ops, admin)
    if runtime is not None and (any(b is not None for b in bags) or overrides):
        raise TypeError("pass runtime=, bags, or flat overrides — not a mix")
    if isinstance(runtime, RuntimeBuilder):
        runtime = runtime.build()
    elif runtime is None:
        if overrides:
            if any(b is not None for b in bags):
                raise TypeError("pass bags or flat overrides — not both")
            core, auth, linking, media, site_ops, admin = bundles_from_overrides(**overrides)
        runtime = AppRuntime(
            core=core,
            auth=auth,
            linking=linking,
            media=media,
            site_ops=site_ops,
            admin=admin,
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
        repo_dir=REPO_DIR,
    )

    metrics_routes.register(
        app,
        service=runtime.service,
        live_audio=runtime.live_audio,
        activity_store=runtime.activity_store,
        process_started_at=time.time(),
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
        restarter=runtime.restarter,
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

    sensors_routes.register(
        app,
        site_sensors=runtime.site_sensors,
        auth_dependencies=runtime.auth_dependencies,
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

    hub_routes.register(
        app,
        hub_sites=runtime.hub_sites,
        hub_poller=runtime.hub_poller,
        admin_dependencies=runtime.admin_dependencies,
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
        bind_stream_identity=runtime.bind_stream_identity,
        auth_enabled=runtime.auth_enabled,
        status_response=_status_response,
        web_dir=WEB_DIR,
    )

    return app



_LOG_HUB = LogHub()
app = create_app(
    core=CoreBundle(
        state_store=StateStore(DEFAULT_STATE_PATH),
        activity_store=ActivityStore(DEFAULT_ACTIVITY_PATH),
    ),
    auth=AuthBundle(
        users=UserStore(StateStore(DEFAULT_USERS_PATH)),
        tokens=TokenStore(StateStore(DEFAULT_TOKENS_PATH)),
        agent_access=AgentAccess(StateStore(DEFAULT_AGENT_ACCESS_PATH)),
        audit=AuditLog(DEFAULT_AUDIT_PATH),
        control_codes=ControlCodes(StateStore(DEFAULT_CONTROL_CODES_PATH)),
    ),
    linking=LinkingBundle(
        node_directory=NodeDirectory(DEFAULT_NODE_LIST_PATH),
        monitor_recordings=RecordingStore(DEFAULT_MONITOR_RECORDINGS_DIR),
    ),
    media=MediaBundle(
        recordings=RecordingStore(DEFAULT_RECORDINGS_DIR),
        mailbox_store=MailboxStore(DEFAULT_MAILBOX_DIR, StateStore(DEFAULT_MAILBOX_DIR / "boxes.json")),
        stream_store=StateStore(DEFAULT_STREAM_PATH),
    ),
    site_ops=SiteOpsBundle(
        aprs_stations=StationStore(DEFAULT_APRS_PATH),
        sensor_history=SensorHistoryStore(DEFAULT_SENSORS_PATH),
        hub_sites=HubSiteStore(hub_sites_state_store(DEFAULT_HUB_SITES_PATH)),
        net_store=StateStore(DEFAULT_NETS_PATH),
    ),
    admin=AdminOpsBundle(
        backups=BackupFolder(DEFAULT_BACKUP_DIR),
        notifier=Notifier(StateStore(DEFAULT_ALERTS_PATH)),
        log_hub=_LOG_HUB,
        log_shipper=LogShipper(_LOG_HUB, StateStore(DEFAULT_LOG_SHIPPING_PATH)),
        run_marker=RunMarker(StateStore(DEFAULT_RUN_MARKER_PATH)),
        auto_update_store=StateStore(DEFAULT_AUTO_UPDATE_PATH),
    ),
)


def main() -> None:
    import uvicorn

    host = os.environ.get("MOREOPENREPEATER_HOST", "0.0.0.0")
    port = int(os.environ.get("MOREOPENREPEATER_PORT", "8000"))
    uvicorn.run("api.app:app", host=host, port=port)


if __name__ == "__main__":
    main()
