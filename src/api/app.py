"""FastAPI app: REST + WebSocket status API, plus the static dashboard."""
from __future__ import annotations

import asyncio
import collections
import dataclasses
import logging
import os
import shutil
import tempfile
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Awaitable, Callable, NamedTuple, Optional
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from starlette.background import BackgroundTask
from starlette.requests import HTTPConnection

from controller.announcements import Announcement
from controller.events import SendLinkCommand
from controller.macros import Macro
from controller.state_machine import RepeaterConfig
from link.aprs_client import APRSClient, format_frequency_comment, format_position_report, format_status_report
from link.node_link import NodeLinkClient
from playout.renderer import ClipRenderer, UnknownClipError
from playout.tts import TTSError, detect_tts
from playout.wav import encode_wav
from wx.nws import alert_areas, fetch_active_alerts, fetch_zone_geometry, missing_zones, speech_text

from .activity import RETENTION_DAYS, ActivityRecorder, ActivityStore, summarize
from .assets import AssetKind, AudioAssetStore
from .auth import (
    SESSION_COOKIE_NAME,
    SESSION_TTL_SECONDS,
    AuthSettings,
    SessionStore,
    auth_settings_from_env,
    parse_basic_auth_header,
    verify_credentials,
)
from link.asterisk_files import asterisk_files_from_env

from .allstar_audio import AllStarAudio, usrp_listen_from_env, usrp_settings_from_env
from .allstar_node import AllStarNode, AllStarSetupError
from .autopatch import Autopatch, patch_settings_from_env
from .backup import BackupError, BackupFolder, BackupSources, backup_name, open_backup, restore_backup, write_backup
from .sip_trunk import DIAL_STRING as TRUNK_DIAL_STRING
from .sip_trunk import AsteriskSetupError, SipTrunk, TrunkSettings
from .aprs_map import AprsReceiver, StationStore, bearing_degrees, distance_km, map_center, spoken_summary
from .audit import RETENTION_DAYS as AUDIT_RETENTION_DAYS
from .audit import AuditEntry, AuditLog, describe_config_change
from .live_audio import LiveAudio, cm108_from_env, list_audio_devices
from .monitor import MonitorSource
from .recordings import RecordingInfo, RecordingStore
from .users import Role, UserError, UserStore
from .logging_config import configure_logging
from .models import (
    ActivitySummaryResponse,
    AnnouncementFields,
    AprsMapResponse,
    AprsStationResponse,
    AuditEntryResponse,
    AnnouncementResponse,
    AssetResponse,
    AudioDeviceResponse,
    AudioEngineResponse,
    AllStarNodeRequest,
    AllStarStatusResponse,
    AudioPreviewRequest,
    AutopatchDialRequest,
    AutopatchStatusResponse,
    BackupFolderResponse,
    BackupRestoreResponse,
    ConfigResponse,
    ConfigUpdateRequest,
    LoginRequest,
    MacroCreateRequest,
    MacroResponse,
    RecordingResponse,
    SavedBackupResponse,
    SessionResponse,
    UserCreateRequest,
    UserResponse,
    UserUpdateRequest,
    SimulateCOSRequest,
    SimulateCTCSSRequest,
    SimulateDTMFRequest,
    SimulateRemoteKeyedRequest,
    SipTrunkRequest,
    SipTrunkStatusResponse,
    SnapshotImportRequest,
    SnapshotModel,
    StatusResponse,
    TransmissionResponse,
    TTSInfoResponse,
    WeatherAlertResponse,
    WeatherStatusResponse,
)
from .persistence import StateStore
from .service import RepeaterService, StatusSnapshot

WEB_DIR = Path(__file__).resolve().parent.parent.parent / "web"
_REPO_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"


def _resolve_data_root(env: dict, repo_data_dir: Path) -> Path:
    """`MOREOPENREPEATER_DATA_DIR`, if set, overrides the default of a
    `data/` directory next to the repo (which only makes sense for an
    editable/source install, not a real packaged deployment)."""
    return Path(env.get("MOREOPENREPEATER_DATA_DIR") or str(repo_data_dir))


def _resolve_data_dir(env: dict, repo_data_dir: Path) -> Path:
    return _resolve_data_root(env, repo_data_dir) / "audio"


def _resolve_log_path(env: dict, repo_data_dir: Path) -> Path:
    return Path(env.get("MOREOPENREPEATER_LOG_PATH") or str(repo_data_dir / "moreopenrepeater.log"))


DEFAULT_DATA_DIR = _resolve_data_dir(os.environ, _REPO_DATA_DIR)
DEFAULT_STATE_PATH = _resolve_data_root(os.environ, _REPO_DATA_DIR) / "state.json"
DEFAULT_ACTIVITY_PATH = _resolve_data_root(os.environ, _REPO_DATA_DIR) / "activity.db"
DEFAULT_RECORDINGS_DIR = _resolve_data_root(os.environ, _REPO_DATA_DIR) / "recordings"
DEFAULT_USERS_PATH = _resolve_data_root(os.environ, _REPO_DATA_DIR) / "users.json"
DEFAULT_AUDIT_PATH = _resolve_data_root(os.environ, _REPO_DATA_DIR) / "audit.db"
DEFAULT_APRS_PATH = _resolve_data_root(os.environ, _REPO_DATA_DIR) / "aprs.db"
DEFAULT_BACKUP_DIR = Path(
    os.environ.get("MOREOPENREPEATER_BACKUP_DIR") or _resolve_data_root(os.environ, _REPO_DATA_DIR) / "backups"
)
BACKUP_CHECK_SECONDS = 60.0
APRS_PRUNE_SECONDS = 300
ZONE_FETCHES_PER_POLL = 20
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")
# POSTs that don't change anything, left out of the audit log.
_UNAUDITED_PATHS = ("/api/audio/preview",)
DEFAULT_LOG_PATH = _resolve_log_path(os.environ, _REPO_DATA_DIR)
TICK_INTERVAL_SECONDS = 0.05
APRS_DISABLED_POLL_SECONDS = 5.0
LINK_RECONNECT_DELAY_SECONDS = 5.0
LOGIN_FAILURE_DELAY_SECONDS = 1.0
MAX_PREVIEW_CLIP_LENGTH = 2000
ANNOUNCEMENT_POLL_SECONDS = 1.0
WEATHER_DISABLED_POLL_SECONDS = 5.0

_aprs_logger = logging.getLogger("moreopenrepeater.aprs")
_announce_logger = logging.getLogger("moreopenrepeater.announcements")
_weather_logger = logging.getLogger("moreopenrepeater.weather")
_link_logger = logging.getLogger("moreopenrepeater.link")
_auth_logger = logging.getLogger("moreopenrepeater.auth")


class Identity(NamedTuple):
    username: Optional[str]  # None when auth is off
    role: Role


_LOCAL = Identity(None, "admin")


class LinkSettings(NamedTuple):
    """AMI credentials for the local app_rpt node -- read from the
    environment rather than RepeaterConfig/the dashboard, since (unlike the
    rest of the config) this is a deployment credential, not something a
    browser client should be able to read back via GET /api/config."""

    host: str
    port: int
    username: str
    secret: str
    local_node_id: str


def link_settings_from_env() -> Optional[LinkSettings]:
    host = os.environ.get("MOREOPENREPEATER_AMI_HOST")
    if not host:
        return None
    return LinkSettings(
        host=host,
        port=int(os.environ.get("MOREOPENREPEATER_AMI_PORT", "5038")),
        username=os.environ.get("MOREOPENREPEATER_AMI_USER", "admin"),
        secret=os.environ.get("MOREOPENREPEATER_AMI_SECRET", ""),
        local_node_id=os.environ.get("MOREOPENREPEATER_AMI_NODE", ""),
    )


def _status_response(snapshot: StatusSnapshot) -> StatusResponse:
    return StatusResponse(**dataclasses.asdict(snapshot))


def _config_response(service: RepeaterService) -> ConfigResponse:
    return ConfigResponse(**dataclasses.asdict(service.config))


def _macro_response(macro: Macro) -> MacroResponse:
    return MacroResponse(**dataclasses.asdict(macro))


def _asset_response(asset) -> AssetResponse:
    return AssetResponse(**dataclasses.asdict(asset))


def _audit_response(entry: AuditEntry) -> AuditEntryResponse:
    return AuditEntryResponse(
        at=datetime.fromtimestamp(entry.at), actor=entry.actor, action=entry.action, detail=entry.detail, status=entry.status
    )


def _recording_response(recording: RecordingInfo) -> RecordingResponse:
    return RecordingResponse(
        id=recording.id, started_at=datetime.fromtimestamp(recording.started_at), duration=recording.duration
    )


def _announcement_response(service: RepeaterService, announcement: Announcement) -> AnnouncementResponse:
    return AnnouncementResponse(
        **dataclasses.asdict(announcement), next_run=service.next_announcement_run(announcement.id)
    )


def _weather_response(service: RepeaterService) -> WeatherStatusResponse:
    alerts = [
        WeatherAlertResponse(
            id=a.id, event=a.event, severity=a.severity, urgency=a.urgency, headline=a.headline, area=a.area,
            expires=a.expires, ends=a.ends, announced=service.weather_tracker.was_announced(a.id),
            speech=speech_text(a),
        )
        for a in service.weather_alerts
    ]
    return WeatherStatusResponse(
        enabled=service.config.wx_alerts_enabled,
        last_checked=service.weather_last_checked,
        last_error=service.weather_last_error,
        alerts=alerts,
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


def aprs_beacon_packet(config: RepeaterConfig) -> str:
    """Info field for our beacon: a position report if located, else a status."""
    comment = config.aprs_comment
    if config.aprs_frequency_mhz is not None:
        tone = config.aprs_tone_hz if config.aprs_tone_hz is not None else config.require_ctcss_hz
        frequency = format_frequency_comment(config.aprs_frequency_mhz, tone, config.aprs_offset_mhz)
        comment = f"{frequency} {comment}".rstrip()
    if config.aprs_lat is None or config.aprs_lon is None:
        return format_status_report(comment)
    table, code = config.aprs_symbol[0], config.aprs_symbol[1]
    return format_position_report(config.aprs_lat, config.aprs_lon, comment, table, code)


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
    audit: Optional[AuditLog] = None,
    aprs_stations: Optional[StationStore] = None,
    autopatch: Optional[Autopatch] = None,
    sip_trunk: Optional[SipTrunk] = None,
    backups: Optional[BackupFolder] = None,
    allstar_node: Optional[AllStarNode] = None,
) -> FastAPI:
    """`state_store`, `activity_store` and `recordings` default to in-memory
    (or off) so tests never touch the real files under `data/`; the
    module-level `app` below opts in.
    `fetch_weather(lat, lon, contact)` and `fetch_zone(url, contact)` are swappable so tests stay offline."""
    assets_store = assets_store or AudioAssetStore(DEFAULT_DATA_DIR)
    recordings = recordings or RecordingStore(None)
    renderer = renderer or ClipRenderer(assets_store.path_for, tts=detect_tts(), recording_path=recordings.path_for)
    service = service or RepeaterService(state_store=state_store)
    if service.renderer is None:
        service.renderer = renderer
    if service.activity is None:
        service.activity = ActivityRecorder(activity_store or ActivityStore())
    activity_store = service.activity.store
    live_audio = live_audio or LiveAudio(
        service, renderer, cm108=cm108_from_env(os.environ), recordings=recordings
    )
    autopatch = autopatch or Autopatch(service, patch_settings_from_env(os.environ))
    sip_trunk = sip_trunk or SipTrunk(autopatch.settings)
    allstar_node = allstar_node or AllStarNode(
        asterisk_files_from_env(os.environ, backup_dir=DEFAULT_DATA_DIR / "asterisk-backups"),
        autopatch.settings,
        AllStarAudio(service, None),
        listen=usrp_listen_from_env(os.environ),
        address=os.environ.get("MOREOPENREPEATER_USRP_ADDRESS"),
        manual=usrp_settings_from_env(os.environ),
    )
    log_path = log_path or DEFAULT_LOG_PATH
    link_settings = link_settings if link_settings is not None else link_settings_from_env()
    auth_settings = auth_settings if auth_settings is not None else auth_settings_from_env(os.environ)
    configure_logging(log_path)
    sessions = SessionStore()
    users = users if users is not None else UserStore()
    users.reserved_username = auth_settings.username if auth_settings else None
    audit = audit or AuditLog()
    service.audit_hook = lambda actor, action, detail: audit.record(time.time(), actor, action, detail)
    aprs_stations = aprs_stations or StationStore()
    aprs_receiver = AprsReceiver(aprs_stations, lambda: service.config)
    service.add_config_listener(lambda _config: aprs_receiver.settings_changed())

    def aprs_window_start() -> float:
        return time.time() - service.config.aprs_map_hours * 3600

    service.aprs_summary = lambda: spoken_summary(aprs_stations.stations(aprs_window_start()), service.config, time.time())
    backups = backups or BackupFolder(None)
    backup_sources = BackupSources(service, users, assets_store, recordings, activity_store, audit)

    def auth_enabled() -> bool:
        """On once there's an env admin or any stored user -- so adding the
        first user from an open dashboard turns sign-in on."""
        return auth_settings is not None or bool(users)

    def check_login(username: str, password: str) -> Optional[Identity]:
        if auth_settings is not None and verify_credentials(username, password, auth_settings):
            return Identity(username, "admin")
        user = users.authenticate(username, password)
        return Identity(user.username, user.role) if user else None

    def identify(conn: HTTPConnection) -> Optional[Identity]:
        """Accepts either the dashboard's session cookie or a Basic
        `Authorization` header (for curl/scripts). Roles are looked up on
        every request, so a role change or deletion applies immediately."""
        if not auth_enabled():
            return _LOCAL
        credentials = parse_basic_auth_header(conn.headers.get("Authorization"))
        if credentials:
            return check_login(*credentials)
        username = sessions.username_for(conn.cookies.get(SESSION_COOKIE_NAME))
        if username is None:
            return None
        if auth_settings is not None and username == auth_settings.username:
            return Identity(username, "admin")
        user = users.get(username)
        return Identity(username, user.role) if user else None

    def require_auth(request: Request) -> None:
        # No WWW-Authenticate header: it would make browsers pop their
        # native Basic Auth dialog over the dashboard's own login page
        # whenever a fetch() hits an expired session.
        identity = identify(request)
        if identity is None:
            raise HTTPException(status_code=401, detail="Not authenticated")
        request.state.identity = identity
        if identity.role == "viewer" and request.method not in SAFE_METHODS:
            raise HTTPException(status_code=403, detail="Your account is read-only")

    def require_admin(request: Request) -> None:
        if request.state.identity.role != "admin":
            raise HTTPException(status_code=403, detail="Only admins can do that")

    auth_dependencies = [Depends(require_auth)]
    admin_dependencies = [Depends(require_auth), Depends(require_admin)]

    async def node_link_loop() -> None:
        assert link_settings is not None
        while True:
            client = NodeLinkClient(
                link_settings.host,
                link_settings.port,
                link_settings.username,
                link_settings.secret,
                link_settings.local_node_id,
            )
            try:
                await client.connect()
                _link_logger.info(
                    "connected to app_rpt AMI at %s:%s (node %s)",
                    link_settings.host,
                    link_settings.port,
                    link_settings.local_node_id,
                )

                def sink(command: SendLinkCommand, _client: NodeLinkClient = client) -> None:
                    asyncio.create_task(_client.send_macro_command(command.node_id, command.command))

                service.set_link_command_sink(sink)
                async for event in client.events():
                    service.handle_link_event(event)
            except (OSError, ConnectionError):
                _link_logger.exception("app_rpt AMI connection lost; reconnecting in %ss", LINK_RECONNECT_DELAY_SECONDS)
            finally:
                await client.close()
            await asyncio.sleep(LINK_RECONNECT_DELAY_SECONDS)

    async def send_aprs_beacon() -> None:
        config = service.config
        client = APRSClient(config.aprs_server, config.aprs_port, config.aprs_callsign)
        await client.connect()
        try:
            await client.send_packet(aprs_beacon_packet(config))
        finally:
            await client.close()

    async def aprs_beacon_loop() -> None:
        while True:
            config = service.config
            if not (config.aprs_enabled and config.aprs_callsign):
                await asyncio.sleep(APRS_DISABLED_POLL_SECONDS)
                continue
            try:
                await send_aprs_beacon()
                _aprs_logger.info("APRS beacon sent to %s:%s", config.aprs_server, config.aprs_port)
            except OSError:
                _aprs_logger.exception("APRS beacon failed")
            await asyncio.sleep(config.aprs_beacon_interval)

    async def render_and_queue(clip: str) -> None:
        """Render off the event loop first, so the controller knows how long
        the clip is (and a TTS failure surfaces here, not mid-transmission)."""
        await asyncio.get_running_loop().run_in_executor(None, renderer.render, clip, service.config)
        if not service.queue_announcement(clip):
            raise HTTPException(status_code=429, detail="Too many announcements are already waiting to play")

    async def aprs_prune_loop() -> None:
        while True:
            await asyncio.sleep(APRS_PRUNE_SECONDS)
            await asyncio.get_running_loop().run_in_executor(None, aprs_stations.prune, aprs_window_start())

    async def announcement_loop() -> None:
        while True:
            await asyncio.sleep(ANNOUNCEMENT_POLL_SECONDS)
            for clip in service.due_announcement_clips():
                try:
                    await render_and_queue(clip)
                except (UnknownClipError, TTSError, HTTPException):
                    _announce_logger.exception("couldn't play scheduled announcement %s", clip)

    async def check_weather(announce: bool) -> None:
        config = service.config
        if config.wx_lat is None or config.wx_lon is None:
            raise HTTPException(status_code=400, detail="Set the station location for weather alerts first")
        try:
            data = await asyncio.get_running_loop().run_in_executor(
                None, fetch_weather, config.wx_lat, config.wx_lon, config.callsign
            )
        except (OSError, ValueError) as error:  # URLError/timeouts are OSErrors; bad JSON is a ValueError
            _weather_logger.warning("NWS alert check failed: %s", error)
            service.weather_poll_failed(str(error))
            return
        for clip in service.weather_polled(data, announce=announce):
            try:
                await render_and_queue(clip)
            except (UnknownClipError, TTSError, HTTPException):
                _weather_logger.exception("couldn't announce weather alert %s", clip)
        if config.aprs_map_enabled:
            await fetch_zone_shapes(config.callsign)

    zone_shapes: dict[str, dict] = {}

    async def fetch_zone_shapes(contact: str) -> None:
        alerts = service.weather_alerts
        wanted = {z for a in alerts for z in a.zones}
        for zone in [z for z in zone_shapes if z not in wanted]:
            del zone_shapes[zone]
        for zone in missing_zones(alerts, zone_shapes)[:ZONE_FETCHES_PER_POLL]:
            try:
                shape = await asyncio.get_running_loop().run_in_executor(None, fetch_zone, zone, contact)
            except Exception as error:  # noqa: BLE001 -- the map just goes without that outline
                _weather_logger.warning("couldn't fetch NWS zone %s: %s", zone, error)
                continue
            if shape:
                zone_shapes[zone] = shape

    async def weather_loop() -> None:
        while True:
            config = service.config
            if not (config.wx_alerts_enabled and config.wx_lat is not None and config.wx_lon is not None):
                await asyncio.sleep(WEATHER_DISABLED_POLL_SECONDS)
                continue
            try:
                await check_weather(announce=True)
            except Exception:
                # Keep polling: a missed severe-weather alert is worse than a noisy log.
                _weather_logger.exception("unexpected error checking NWS alerts")
            await asyncio.sleep(config.wx_poll_interval)

    async def backup_loop() -> None:
        loop = asyncio.get_running_loop()
        while True:
            await asyncio.sleep(BACKUP_CHECK_SECONDS)
            await loop.run_in_executor(None, backups.run_schedule, backup_sources, service.config, time.time())

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        tasks: list[asyncio.Task] = []
        if start_background_tick:
            async def tick_loop() -> None:
                while True:
                    await asyncio.sleep(TICK_INTERVAL_SECONDS)
                    service.tick()

            tasks.append(asyncio.create_task(tick_loop()))
            tasks.append(asyncio.create_task(aprs_beacon_loop()))
            tasks.append(asyncio.create_task(announcement_loop()))
            tasks.append(asyncio.create_task(weather_loop()))
            tasks.append(asyncio.create_task(aprs_receiver.run()))
            tasks.append(asyncio.create_task(aprs_prune_loop()))
            activity_store.prune((datetime.now() - timedelta(days=RETENTION_DAYS)).timestamp())
            audit.prune((datetime.now() - timedelta(days=AUDIT_RETENTION_DAYS)).timestamp())
            loop = asyncio.get_running_loop()
            loop.run_in_executor(None, renderer.warm, service.config)
            service.add_config_listener(lambda config: loop.run_in_executor(None, renderer.warm, config))
            tasks.append(asyncio.create_task(backup_loop()))
            if link_settings is not None:
                tasks.append(asyncio.create_task(node_link_loop()))
            live_audio.attach(loop)
            await autopatch.start()
            await allstar_node.start()
        yield
        for task in tasks:
            task.cancel()
        await allstar_node.stop()
        await autopatch.stop()
        live_audio.shutdown()

    app = FastAPI(title="moreopenrepeater API", lifespan=lifespan)
    app.state.service = service

    @app.middleware("http")
    async def revalidate_static_files(request: Request, call_next):
        # Without this, browsers heuristically cache app.js/style.css and
        # keep running the old dashboard after an upgrade. "no-cache" still
        # allows caching -- it just forces a cheap ETag revalidation (304).
        response = await call_next(request)
        path = request.url.path
        if not path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-cache")
        elif request.method not in SAFE_METHODS and path not in _UNAUDITED_PATHS:
            state = request.state
            identity = getattr(state, "identity", None)
            actor = getattr(state, "audit_actor", None) or (identity and identity.username)
            audit.record(
                time.time(),
                actor or ("local" if not auth_enabled() else "anonymous"),
                f"{request.method} {path}",
                getattr(state, "audit_detail", ""),
                response.status_code,
            )
        return response

    def session_response(identity: Optional[Identity]) -> SessionResponse:
        return SessionResponse(
            auth_required=auth_enabled(),
            authenticated=identity is not None,
            username=identity.username if identity else None,
            role=identity.role if identity else None,
        )

    @app.get("/api/session", response_model=SessionResponse)
    def get_session(request: Request) -> SessionResponse:
        return session_response(identify(request))

    @app.post("/api/login", response_model=SessionResponse)
    async def login(body: LoginRequest, request: Request, response: Response) -> SessionResponse:
        if not auth_enabled():
            return session_response(_LOCAL)
        request.state.audit_actor = body.username
        # scrypt takes tens of milliseconds; keep it off the event loop.
        identity = await asyncio.get_running_loop().run_in_executor(None, check_login, body.username, body.password)
        if identity is None:
            _auth_logger.warning("failed login for %r from %s", body.username, request.client.host if request.client else "?")
            await asyncio.sleep(LOGIN_FAILURE_DELAY_SECONDS)
            raise HTTPException(status_code=401, detail="Invalid username or password")
        sessions.revoke(request.cookies.get(SESSION_COOKIE_NAME))
        response.set_cookie(
            SESSION_COOKIE_NAME,
            sessions.create(body.username),
            max_age=SESSION_TTL_SECONDS,
            httponly=True,
            samesite="strict",
            secure=request.url.scheme == "https",
        )
        _auth_logger.info("login succeeded for %r", body.username)
        return session_response(identity)

    @app.post("/api/logout", response_model=SessionResponse)
    async def logout(request: Request, response: Response) -> SessionResponse:
        sessions.revoke(request.cookies.get(SESSION_COOKIE_NAME))
        response.delete_cookie(SESSION_COOKIE_NAME, httponly=True, samesite="strict")
        return session_response(None if auth_enabled() else _LOCAL)

    @app.get("/api/status", response_model=StatusResponse, dependencies=auth_dependencies)
    def get_status() -> StatusResponse:
        return _status_response(service.snapshot())

    @app.get("/api/config", response_model=ConfigResponse, dependencies=auth_dependencies)
    def get_config() -> ConfigResponse:
        return _config_response(service)

    @app.put("/api/config", response_model=ConfigResponse, dependencies=auth_dependencies)
    async def put_config(update: ConfigUpdateRequest, request: Request) -> ConfigResponse:
        clear_fields = {name for name in ConfigUpdateRequest.model_fields if name.startswith("clear_")}
        overrides = update.model_dump(exclude=clear_fields, exclude_none=True)
        for clear_field in clear_fields:
            if getattr(update, clear_field):
                overrides[clear_field.removeprefix("clear_")] = None
        request.state.audit_detail = describe_config_change(dataclasses.asdict(service.config), overrides)
        service.update_config(**overrides)
        return _config_response(service)

    @app.post("/api/simulate/cos", response_model=StatusResponse, dependencies=auth_dependencies)
    async def simulate_cos(body: SimulateCOSRequest) -> StatusResponse:
        service.simulate_cos(body.active)
        return _status_response(service.snapshot())

    @app.post("/api/simulate/ctcss", response_model=StatusResponse, dependencies=auth_dependencies)
    async def simulate_ctcss(body: SimulateCTCSSRequest) -> StatusResponse:
        service.simulate_ctcss(body.tone_hz)
        return _status_response(service.snapshot())

    @app.post("/api/simulate/dtmf", response_model=StatusResponse, dependencies=auth_dependencies)
    async def simulate_dtmf(body: SimulateDTMFRequest) -> StatusResponse:
        service.simulate_dtmf(body.digit)
        return _status_response(service.snapshot())

    @app.post("/api/simulate/remote-keyed", response_model=StatusResponse, dependencies=auth_dependencies)
    async def simulate_remote_keyed(body: SimulateRemoteKeyedRequest) -> StatusResponse:
        service.simulate_remote_keyed(body.node_id, body.keyed)
        return _status_response(service.snapshot())

    @app.get("/api/macros", response_model=list[MacroResponse], dependencies=auth_dependencies)
    def list_macros() -> list[MacroResponse]:
        return [_macro_response(m) for m in service.list_macros()]

    @app.post("/api/macros", response_model=list[MacroResponse], dependencies=auth_dependencies)
    async def add_macro(body: MacroCreateRequest) -> list[MacroResponse]:
        macro = Macro(**body.model_dump())
        return [_macro_response(m) for m in service.add_macro(macro)]

    @app.delete("/api/macros/{pattern}", response_model=list[MacroResponse], dependencies=auth_dependencies)
    async def delete_macro(pattern: str) -> list[MacroResponse]:
        if pattern not in {m.pattern for m in service.list_macros()}:
            raise HTTPException(status_code=404, detail=f"No macro with pattern {pattern!r}")
        return [_macro_response(m) for m in service.delete_macro(pattern)]

    def find_announcement(announcement_id: str) -> Announcement:
        for announcement in service.list_announcements():
            if announcement.id == announcement_id:
                return announcement
        raise HTTPException(status_code=404, detail=f"No announcement with id {announcement_id!r}")

    def save_announcement(announcement_id: str, body: AnnouncementFields) -> AnnouncementResponse:
        if body.asset_id and not assets_store.path_for(body.asset_id).exists():
            raise HTTPException(status_code=400, detail=f"No audio clip with id {body.asset_id!r}")
        announcement = Announcement(id=announcement_id, **body.model_dump())
        return _announcement_response(service, service.save_announcement(announcement))

    @app.get("/api/announcements", response_model=list[AnnouncementResponse], dependencies=auth_dependencies)
    def list_announcements() -> list[AnnouncementResponse]:
        return [_announcement_response(service, a) for a in service.list_announcements()]

    @app.post("/api/announcements", response_model=AnnouncementResponse, dependencies=auth_dependencies)
    async def create_announcement(body: AnnouncementFields) -> AnnouncementResponse:
        return save_announcement(uuid.uuid4().hex, body)

    @app.put("/api/announcements/{announcement_id}", response_model=AnnouncementResponse, dependencies=auth_dependencies)
    async def update_announcement(announcement_id: str, body: AnnouncementFields) -> AnnouncementResponse:
        find_announcement(announcement_id)
        return save_announcement(announcement_id, body)

    @app.delete("/api/announcements/{announcement_id}", dependencies=auth_dependencies)
    async def delete_announcement(announcement_id: str) -> dict:
        find_announcement(announcement_id)
        service.delete_announcement(announcement_id)
        return {"deleted": announcement_id}

    @app.post("/api/announcements/{announcement_id}/play", response_model=StatusResponse, dependencies=auth_dependencies)
    async def play_announcement(announcement_id: str) -> StatusResponse:
        clip = service.announcement_clip(find_announcement(announcement_id))
        try:
            await render_and_queue(clip)
        except UnknownClipError:
            raise HTTPException(status_code=404, detail="The announcement's audio clip no longer exists")
        except TTSError as error:
            raise HTTPException(status_code=503, detail=str(error))
        return _status_response(service.snapshot())

    @app.get("/api/weather", response_model=WeatherStatusResponse, dependencies=auth_dependencies)
    def get_weather() -> WeatherStatusResponse:
        return _weather_response(service)

    @app.get("/api/weather/areas", dependencies=auth_dependencies)
    def get_weather_areas() -> dict:
        return alert_areas(service.weather_alerts, zone_shapes)

    @app.post("/api/weather/check", response_model=WeatherStatusResponse, dependencies=auth_dependencies)
    async def check_weather_now() -> WeatherStatusResponse:
        await check_weather(announce=service.config.wx_alerts_enabled)
        return _weather_response(service)

    @app.post("/api/weather/alerts/{alert_id}/play", response_model=StatusResponse, dependencies=auth_dependencies)
    async def play_weather_alert(alert_id: str) -> StatusResponse:
        alert = next((a for a in service.weather_alerts if a.id == alert_id), None)
        if alert is None:
            raise HTTPException(status_code=404, detail="That alert is no longer active")
        try:
            await render_and_queue(service.weather_alert_clip(alert))
        except TTSError as error:
            raise HTTPException(status_code=503, detail=str(error))
        return _status_response(service.snapshot())

    @app.get("/api/activity/summary", response_model=ActivitySummaryResponse, dependencies=auth_dependencies)
    def activity_summary(days: int = Query(default=7, ge=1, le=RETENTION_DAYS)) -> dict:
        until = service.wall_now()
        since = until - timedelta(days=days)
        return summarize(activity_store.rows(since.timestamp(), until.timestamp()), since, until)

    @app.get("/api/activity/transmissions", response_model=list[TransmissionResponse], dependencies=auth_dependencies)
    def recent_transmissions(limit: int = Query(default=50, ge=1, le=500)) -> list[TransmissionResponse]:
        return [
            TransmissionResponse(started_at=datetime.fromtimestamp(r.started_at), duration=r.duration, timed_out=r.timed_out)
            for r in activity_store.recent("rx", limit)
        ]

    @app.get("/api/snapshot", response_model=SnapshotModel, dependencies=auth_dependencies)
    def get_snapshot() -> SnapshotModel:
        return SnapshotModel(**service.export_snapshot())

    @app.post("/api/snapshot", response_model=SnapshotModel, dependencies=auth_dependencies)
    async def post_snapshot(snapshot: SnapshotImportRequest) -> SnapshotModel:
        service.import_snapshot(_snapshot_to_import(snapshot))
        return SnapshotModel(**service.export_snapshot())

    def restore_backup_file(path: Path, loop: asyncio.AbstractEventLoop) -> dict:
        """Runs in a worker thread; the settings themselves are applied on the event loop."""

        async def import_on_loop(settings: dict) -> None:
            service.import_snapshot(settings)

        def import_settings(settings: dict) -> None:
            asyncio.run_coroutine_threadsafe(import_on_loop(settings), loop).result()

        try:
            with open_backup(path) as backup:
                try:
                    settings = _snapshot_to_import(SnapshotImportRequest.model_validate(backup.settings))
                except (ValidationError, RequestValidationError):
                    raise BackupError("The settings in the backup aren't valid for this version.") from None
                return restore_backup(backup, backup_sources, settings, import_settings)
        except (BackupError, UserError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from None

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
        audit.record(now, request.state.identity.username or "local", "download backup", "with recordings" if recordings else "")
        return FileResponse(
            path,
            media_type="application/zip",
            filename=backup_name(service.config.callsign, now),
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
        audit.record(time.time(), request.state.identity.username or "local", "download backup", name)
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

    @app.get("/api/audio/tts", response_model=TTSInfoResponse, dependencies=auth_dependencies)
    def get_tts_info() -> TTSInfoResponse:
        return TTSInfoResponse(engine=renderer.tts.name if renderer.tts is not None else None)

    # Plain `def`: FastAPI runs it in a worker thread, so a slow TTS render
    # doesn't stall the event loop (and with it the controller tick).
    @app.post("/api/audio/preview", response_class=Response, dependencies=auth_dependencies)
    def audio_preview(body: AudioPreviewRequest) -> Response:
        if len(body.clip) > MAX_PREVIEW_CLIP_LENGTH:
            raise HTTPException(status_code=400, detail="Text is too long to preview")
        config = _apply_overrides(service.config, body.config)
        try:
            samples = renderer.render(body.clip, config)
        except UnknownClipError:
            raise HTTPException(status_code=404, detail=f"Unknown clip {body.clip!r}")
        except TTSError as error:
            raise HTTPException(status_code=503, detail=str(error))
        return Response(encode_wav(samples, renderer.sample_rate), media_type="audio/wav")

    @app.get("/api/audio/devices", response_model=list[AudioDeviceResponse], dependencies=auth_dependencies)
    def get_audio_devices() -> list[dict]:
        try:
            return audio_devices()
        except Exception as error:  # PortAudio errors
            raise HTTPException(status_code=503, detail=f"couldn't list audio devices: {error}")

    async def change_allstar(change: Awaitable[dict]) -> dict:
        try:
            return await change
        except AllStarSetupError as error:
            raise HTTPException(status_code=502, detail=str(error))

    @app.get("/api/allstar", response_model=AllStarStatusResponse, dependencies=auth_dependencies)
    async def get_allstar() -> dict:
        return await allstar_node.status()

    @app.put("/api/allstar", response_model=AllStarStatusResponse, dependencies=admin_dependencies)
    async def put_allstar(body: AllStarNodeRequest, request: Request) -> dict:
        no_call_in_progress()
        request.state.audit_detail = f"node {body.node}"
        return await change_allstar(allstar_node.use(body.node))

    @app.delete("/api/allstar", response_model=AllStarStatusResponse, dependencies=admin_dependencies)
    async def delete_allstar() -> dict:
        no_call_in_progress()
        return await change_allstar(allstar_node.release())

    @app.get("/api/audio/engine", response_model=AudioEngineResponse, dependencies=auth_dependencies)
    def get_audio_engine() -> dict:
        return live_audio.status()

    @app.get("/api/autopatch", response_model=AutopatchStatusResponse, dependencies=auth_dependencies)
    def get_autopatch() -> dict:
        return autopatch.status()

    @app.post("/api/autopatch/dial", response_model=AutopatchStatusResponse, dependencies=auth_dependencies)
    async def autopatch_dial(body: AutopatchDialRequest, request: Request) -> dict:
        request.state.audit_detail = body.number
        error = autopatch.dial(body.number, request.state.identity.username or "local")
        if error is not None:
            raise HTTPException(status_code=409, detail=error)
        return autopatch.status()

    @app.post("/api/autopatch/hangup", response_model=AutopatchStatusResponse, dependencies=auth_dependencies)
    async def autopatch_hangup(request: Request) -> dict:
        autopatch.hangup(f"hung up from the dashboard by {request.state.identity.username or 'local'}")
        return autopatch.status()

    async def trunk_status() -> dict:
        config = service.config
        return {
            **await sip_trunk.status(),
            "in_use": config.autopatch_dial_string == TRUNK_DIAL_STRING,
            "ten_digit_prefix": config.autopatch_ten_digit_prefix,
        }

    async def change_trunk(change: Awaitable[None]) -> dict:
        try:
            await change
        except AsteriskSetupError as error:
            raise HTTPException(status_code=502, detail=str(error))
        return await trunk_status()

    def no_call_in_progress() -> None:
        if autopatch.call is not None:
            raise HTTPException(status_code=409, detail="Hang up the call first.")

    @app.get("/api/autopatch/trunk", response_model=SipTrunkStatusResponse, dependencies=auth_dependencies)
    async def get_trunk() -> dict:
        return await trunk_status()

    @app.post("/api/autopatch/trunk/modules", response_model=SipTrunkStatusResponse, dependencies=admin_dependencies)
    async def enable_trunk_modules() -> dict:
        return await change_trunk(sip_trunk.enable_modules())

    @app.put("/api/autopatch/trunk", response_model=SipTrunkStatusResponse, dependencies=admin_dependencies)
    async def put_trunk(body: SipTrunkRequest, request: Request) -> dict:
        no_call_in_progress()
        request.state.audit_detail = f"{body.username}@{body.server}" + (", new password" if body.password else "")
        trunk = TrunkSettings(
            server=body.server,
            port=body.port,
            transport=body.transport,
            username=body.username,
            auth_username=body.auth_username,
            password=body.password or None,
            registers=body.registers,
            incoming_from=tuple(body.incoming_from),
        )
        status = await change_trunk(sip_trunk.save(trunk))
        service.update_config(autopatch_dial_string=TRUNK_DIAL_STRING, autopatch_ten_digit_prefix=body.ten_digit_prefix)
        return {**status, "in_use": True, "ten_digit_prefix": body.ten_digit_prefix}

    @app.delete("/api/autopatch/trunk", response_model=SipTrunkStatusResponse, dependencies=admin_dependencies)
    async def delete_trunk() -> dict:
        no_call_in_progress()
        return await change_trunk(sip_trunk.remove())

    @app.get("/api/assets", response_model=list[AssetResponse], dependencies=auth_dependencies)
    def list_assets() -> list[AssetResponse]:
        return [_asset_response(a) for a in assets_store.list_assets()]

    @app.post("/api/assets", response_model=AssetResponse, dependencies=auth_dependencies)
    async def upload_asset(kind: AssetKind = Form(...), file: UploadFile = File(...)) -> AssetResponse:
        if not (file.filename or "").lower().endswith(".wav"):
            raise HTTPException(status_code=400, detail="Only .wav files are accepted")
        content = await file.read()
        asset = assets_store.save_asset(kind, file.filename, content)
        return _asset_response(asset)

    @app.delete("/api/assets/{asset_id}", dependencies=auth_dependencies)
    def delete_asset(asset_id: str) -> dict:
        if asset_id not in {a.id for a in assets_store.list_assets()}:
            raise HTTPException(status_code=404, detail=f"No asset with id {asset_id!r}")
        assets_store.delete_asset(asset_id)
        return {"deleted": asset_id}

    @app.get("/api/assets/{asset_id}/audio", dependencies=auth_dependencies)
    def get_asset_audio(asset_id: str) -> FileResponse:
        path = assets_store.path_for(asset_id)
        if not path.exists():
            raise HTTPException(status_code=404, detail=f"No asset with id {asset_id!r}")
        return FileResponse(path, media_type="audio/wav")

    def recording_path(recording_id: str) -> Path:
        try:
            path = recordings.path_for(recording_id)
        except KeyError:
            path = None
        if path is None or not path.exists():
            raise HTTPException(status_code=404, detail=f"No recording with id {recording_id!r}")
        return path

    @app.get("/api/recordings", response_model=list[RecordingResponse], dependencies=auth_dependencies)
    def list_recordings(limit: int = 50) -> list[RecordingResponse]:
        return [_recording_response(r) for r in recordings.list(limit)]

    @app.get("/api/recordings/{recording_id}/audio", dependencies=auth_dependencies)
    def get_recording_audio(recording_id: str) -> FileResponse:
        return FileResponse(recording_path(recording_id), media_type="audio/wav")

    @app.delete("/api/recordings/{recording_id}", dependencies=auth_dependencies)
    def delete_recording(recording_id: str) -> dict:
        recording_path(recording_id)
        recordings.delete(recording_id)
        return {"deleted": recording_id}

    @app.get("/api/logs", response_model=list[str], dependencies=auth_dependencies)
    def get_logs(lines: int = 200) -> list[str]:
        if not log_path.exists():
            return []
        with log_path.open() as f:
            return [line.rstrip("\n") for line in collections.deque(f, maxlen=lines)]

    @app.get("/api/aprs/stations", response_model=AprsMapResponse, dependencies=auth_dependencies)
    def get_aprs_stations() -> AprsMapResponse:
        config = service.config
        center = map_center(config)
        status = aprs_receiver.status
        stations = []
        for s in aprs_stations.stations(aprs_window_start()):
            fields = dataclasses.asdict(s)
            fields["trail"] = [[lat, lon] for _at, lat, lon in s.trail]
            if center is not None:
                fields["distance_km"] = round(distance_km(*center, s.lat, s.lon), 2)
                fields["bearing"] = round(bearing_degrees(*center, s.lat, s.lon))
            stations.append(AprsStationResponse(**fields))
        return AprsMapResponse(
            enabled=config.aprs_map_enabled,
            center=list(center) if center else None,
            radius_km=config.aprs_map_radius_km,
            hours=config.aprs_map_hours,
            tiles=config.aprs_map_tiles,
            distance_units=config.distance_units,
            connected=status.connected,
            server=status.server,
            error=status.error,
            last_packet=datetime.fromtimestamp(status.last_packet) if status.last_packet else None,
            packets=status.packets,
            stations=stations,
        )

    @app.get("/api/users", response_model=list[UserResponse], dependencies=admin_dependencies)
    def list_users() -> list[UserResponse]:
        builtin = [UserResponse(username=auth_settings.username, role="admin", builtin=True)] if auth_settings else []
        return builtin + [UserResponse(username=u.username, role=u.role, builtin=False) for u in users.list()]

    @app.post("/api/users", response_model=list[UserResponse], dependencies=admin_dependencies)
    async def create_user(body: UserCreateRequest, request: Request) -> list[UserResponse]:
        request.state.audit_detail = f"{body.username} ({body.role})"
        try:
            await asyncio.get_running_loop().run_in_executor(None, users.add, body.username, body.password, body.role)
        except UserError as error:
            raise HTTPException(status_code=400, detail=str(error)) from None
        return list_users()

    @app.put("/api/users/{username}", response_model=list[UserResponse], dependencies=admin_dependencies)
    async def update_user(username: str, body: UserUpdateRequest, request: Request) -> list[UserResponse]:
        changes = ([f"role → {body.role}"] if body.role else []) + (["password reset"] if body.password else [])
        request.state.audit_detail = f"{username}: {', '.join(changes)}"
        try:
            await asyncio.get_running_loop().run_in_executor(None, users.update, username, body.password, body.role)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"No user {username!r}") from None
        except UserError as error:
            raise HTTPException(status_code=400, detail=str(error)) from None
        return list_users()

    @app.delete("/api/users/{username}", response_model=list[UserResponse], dependencies=admin_dependencies)
    def delete_user(username: str) -> list[UserResponse]:
        try:
            users.delete(username)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"No user {username!r}") from None
        except UserError as error:
            raise HTTPException(status_code=400, detail=str(error)) from None
        return list_users()

    @app.get("/api/audit", response_model=list[AuditEntryResponse], dependencies=admin_dependencies)
    def get_audit(limit: int = Query(default=200, ge=1, le=2000)) -> list[AuditEntryResponse]:
        return [_audit_response(e) for e in audit.recent(limit)]

    def websocket_allowed(websocket: WebSocket) -> bool:
        if not auth_enabled():
            return True
        # WebSocket handshakes aren't subject to CORS, so a page on another
        # origin could otherwise open this with the user's session cookie.
        origin = websocket.headers.get("origin")
        if origin and urlsplit(origin).netloc != websocket.headers.get("host"):
            return False
        return identify(websocket) is not None

    @app.websocket("/ws/audio")
    async def ws_audio(websocket: WebSocket, source: MonitorSource = "tx") -> None:
        """Binary frames of 16-bit little-endian mono PCM at the processing rate."""
        if not websocket_allowed(websocket):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        queue = live_audio.monitor.subscribe(source)

        async def send_frames() -> None:
            await websocket.send_json({"sample_rate": renderer.sample_rate, "source": source})
            while True:
                await websocket.send_bytes(await queue.get())

        # Frames may never come (engine off), so watch for the disconnect separately.
        sender = asyncio.create_task(send_frames())
        try:
            while (await websocket.receive())["type"] != "websocket.disconnect":
                pass
        finally:
            sender.cancel()
            live_audio.monitor.unsubscribe(queue)

    @app.websocket("/ws/status")
    async def ws_status(websocket: WebSocket) -> None:
        if not websocket_allowed(websocket):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        queue = service.subscribe()
        try:
            await websocket.send_json(_status_response(service.snapshot()).model_dump())
            while True:
                snapshot = await queue.get()
                await websocket.send_json(_status_response(snapshot).model_dump())
        except WebSocketDisconnect:
            pass
        finally:
            service.unsubscribe(queue)

    if WEB_DIR.is_dir():
        def is_signed_in(request: Request) -> bool:
            return identify(request) is not None

        @app.get("/", response_model=None)
        def index(request: Request) -> Response:
            if not is_signed_in(request):
                return RedirectResponse("/login", status_code=303)
            return FileResponse(WEB_DIR / "index.html")

        @app.get("/login", response_model=None)
        def login_page(request: Request) -> Response:
            if not auth_enabled() or is_signed_in(request):
                return RedirectResponse("/", status_code=303)
            return FileResponse(WEB_DIR / "login.html")

        # Left unauthenticated: the JS/CSS hold no secrets (every piece of
        # repeater data comes from the authenticated API), and a Mount is a
        # raw ASGI sub-application that can't take route-level
        # `dependencies=` anyway.
        app.mount("/", StaticFiles(directory=WEB_DIR), name="web")

    return app


app = create_app(
    state_store=StateStore(DEFAULT_STATE_PATH),
    activity_store=ActivityStore(DEFAULT_ACTIVITY_PATH),
    recordings=RecordingStore(DEFAULT_RECORDINGS_DIR),
    users=UserStore(StateStore(DEFAULT_USERS_PATH)),
    audit=AuditLog(DEFAULT_AUDIT_PATH),
    aprs_stations=StationStore(DEFAULT_APRS_PATH),
    backups=BackupFolder(DEFAULT_BACKUP_DIR),
)


def main() -> None:
    import uvicorn

    host = os.environ.get("MOREOPENREPEATER_HOST", "0.0.0.0")
    port = int(os.environ.get("MOREOPENREPEATER_PORT", "8000"))
    uvicorn.run("api.app:app", host=host, port=port)


if __name__ == "__main__":
    main()
