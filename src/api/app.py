"""FastAPI app: REST + WebSocket status API, plus the static dashboard."""
from __future__ import annotations

import asyncio
import collections
import dataclasses
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, NamedTuple, Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from starlette.requests import HTTPConnection
from starlette.routing import Route

from audio_io.serial_lines import find_serial_ports
from controller.events import LinkStateChanged, SendLinkCommand
from controller.state_machine import RepeaterConfig
from link.aprs_client import APRSClient, format_frequency_comment, format_position_report, format_status_report
from link.asterisk_files import asterisk_files_from_env
from link.node_link import NodeLinkClient
from playout.renderer import ClipRenderer, UnknownClipError
from playout.tts import TTSError, detect_tts
from wx.nws import fetch_active_alerts, fetch_zone_geometry, missing_zones

from .activity import FLUSH_SECONDS as ACTIVITY_FLUSH_SECONDS
from .activity import RETENTION_DAYS, ActivityRecorder, ActivityStore
from .agents import MCP_PATH, AgentAccess, AgentEndpoint, build_server
from .airport_weather import AirportWeather
from .allstar_audio import AllStarAudio, usrp_listen_from_env, usrp_settings_from_env
from .allstar_node import AllStarNode
from .aprs_map import AprsReceiver, StationStore, spoken_summary
from .assets import AudioAssetStore
from .audit import RETENTION_DAYS as AUDIT_RETENTION_DAYS
from .audit import AuditEntry, AuditLog
from .auth import (
    SESSION_COOKIE_NAME,
    AuthSettings,
    SessionStore,
    auth_settings_from_env,
    parse_basic_auth_header,
    verify_credentials,
)
from .auto_update import POLL_SECONDS as AUTO_UPDATE_POLL_SECONDS, AutoUpdater
from .autopatch import Autopatch, patch_settings_from_env
from .backup import BackupError, BackupFolder, BackupSources, open_backup, restore_backup
from .boards import run_amixer
from .control_codes import ControlCodes
from .gpio import GpioControl
from .health import CHECK_SECONDS as HEALTH_CHECK_SECONDS
from .health import HealthMonitor, RunMarker, SystemProbe, lockout_alert
from .homeassistant import TOKEN_ENV as HOMEASSISTANT_TOKEN_ENV
from .homeassistant import HomeAssistant
from .link_radio import LinkRadio
from .link_schedule import POLL_SECONDS as LINK_SCHEDULE_POLL_SECONDS, LinkScheduler
from .links import LinkControl, LinkError
from .live_audio import LiveAudio, cm108_from_env, link_cm108_from_env, list_audio_devices
from .logging_config import configure_logging
from .mailbox import Mailbox, MailboxStore, is_mailbox_clip
from .models import AuditEntryResponse, ConfigResponse, SessionResponse, SnapshotImportRequest, StatusResponse
from .monitor_receiver import MonitorReceiverService
from .net import POLL_SECONDS as NET_POLL_SECONDS
from .net import NetMode
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
from .service import RepeaterService, StatusSnapshot
from .sip_trunk import SipTrunk
from .stream import Streamer
from .tokens import TokenStore
from .transcripts import API_KEY_ENV as TRANSCRIPTION_KEY_ENV
from .transcripts import VOSK_MODEL_ENV, Transcriber, VoskEngine
from .tx_fan import TxFan
from .updates import UpdateChecker, Updater, channel_from_env, installed_version, repo_from_checkout, updater_settings_from_env
from .users import Role, UserError, UserStore
from .watchdog import Watchdog, ping_interval, sd_notify

REPO_DIR = Path(__file__).resolve().parent.parent.parent
WEB_DIR = REPO_DIR / "web"
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
DEFAULT_MAILBOX_DIR = _resolve_data_root(os.environ, _REPO_DATA_DIR) / "mailbox"
DEFAULT_MONITOR_RECORDINGS_DIR = _resolve_data_root(os.environ, _REPO_DATA_DIR) / "monitor-recordings"
MAILBOX_TICK_SECONDS = 60.0
DEFAULT_VOSK_MODEL_DIR = _resolve_data_root(os.environ, _REPO_DATA_DIR) / "vosk-model"
TRANSCRIBE_POLL_SECONDS = 15.0
DEFAULT_USERS_PATH = _resolve_data_root(os.environ, _REPO_DATA_DIR) / "users.json"
DEFAULT_TOKENS_PATH = _resolve_data_root(os.environ, _REPO_DATA_DIR) / "tokens.json"
DEFAULT_AGENT_ACCESS_PATH = _resolve_data_root(os.environ, _REPO_DATA_DIR) / "agent-access.json"
DEFAULT_AUDIT_PATH = _resolve_data_root(os.environ, _REPO_DATA_DIR) / "audit.db"
DEFAULT_APRS_PATH = _resolve_data_root(os.environ, _REPO_DATA_DIR) / "aprs.db"
DEFAULT_NODE_LIST_PATH = _resolve_data_root(os.environ, _REPO_DATA_DIR) / "allstar-nodes.txt"
DEFAULT_ALERTS_PATH = _resolve_data_root(os.environ, _REPO_DATA_DIR) / "alerts.json"
DEFAULT_RUN_MARKER_PATH = _resolve_data_root(os.environ, _REPO_DATA_DIR) / "run-state.json"
DEFAULT_NETS_PATH = _resolve_data_root(os.environ, _REPO_DATA_DIR) / "nets.json"
DEFAULT_AUTO_UPDATE_PATH = _resolve_data_root(os.environ, _REPO_DATA_DIR) / "auto-update.json"
DEFAULT_CONTROL_CODES_PATH = _resolve_data_root(os.environ, _REPO_DATA_DIR) / "control-codes.json"
DEFAULT_STREAM_PATH = _resolve_data_root(os.environ, _REPO_DATA_DIR) / "stream.json"
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
ANNOUNCEMENT_POLL_SECONDS = 1.0
WEATHER_DISABLED_POLL_SECONDS = 5.0
LONG_ID_WARM_SECONDS = 20.0

_aprs_logger = logging.getLogger("moreopenrepeater.aprs")
_announce_logger = logging.getLogger("moreopenrepeater.announcements")
_weather_logger = logging.getLogger("moreopenrepeater.weather")
_link_logger = logging.getLogger("moreopenrepeater.link")


class Identity(NamedTuple):
    username: Optional[str]  # None when auth is off
    role: Role
    token: Optional[str] = None  # the API token's name, when signed in with one

    @property
    def actor(self) -> Optional[str]:
        """Who the audit log says did it."""
        return f"{self.username} (token {self.token})" if self.token and self.username else self.username


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
    notifier: Optional[Notifier] = None,
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
    log_path = log_path or DEFAULT_LOG_PATH
    configure_logging(log_path)
    assets_store = assets_store or AudioAssetStore(DEFAULT_DATA_DIR)
    recordings = recordings or RecordingStore(None)
    mailbox_store = mailbox_store or MailboxStore(None, None)
    monitor_recordings = monitor_recordings or RecordingStore(None)

    def clip_recording_path(clip_id: str) -> Path:
        return mailbox_store.path_for(clip_id) if is_mailbox_clip(clip_id) else recordings.path_for(clip_id)

    renderer = renderer or ClipRenderer(assets_store.path_for, tts=detect_tts(), recording_path=clip_recording_path)
    service = service or RepeaterService(state_store=state_store)
    if service.renderer is None:
        service.renderer = renderer
    if service.activity is None:
        service.activity = ActivityRecorder(activity_store or ActivityStore())
    activity_store = service.activity.store
    live_audio = live_audio or LiveAudio(
        service, renderer, cm108=cm108_from_env(os.environ), recordings=recordings, run_mixer=run_mixer
    )
    monitor_receiver = monitor_receiver or MonitorReceiverService(service, renderer.sample_rate, monitor_recordings)
    cm108 = live_audio.cm108
    link_radio = link_radio or LinkRadio(
        service,
        renderer,
        sending=lambda: live_audio.repeating_voice,
        attach_port=live_audio.set_port,
        find_cm108=lambda: link_cm108_from_env(os.environ, cm108),
    )
    remote_base = remote_base or RemoteBase(service)
    gpio = gpio or GpioControl(service, cm108)
    service.gpio_command = gpio.run_command
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
    link_settings = link_settings if link_settings is not None else link_settings_from_env()
    node_directory = node_directory if node_directory is not None else NodeDirectory(None)
    links = links or LinkControl(
        service, node_directory, lambda: (link_settings.local_node_id if link_settings else "") or allstar_node.node or ""
    )
    link_scheduler = LinkScheduler(links, lambda: service.config.link_schedules)
    nets = NetMode(service, links, net_store)
    gmrs_mode = [service.saved_config.gmrs_mode]

    async def drop_links(node: Optional[str] = None) -> None:
        try:
            await (links.disconnect(node) if node else links.disconnect_all())
        except LinkError as error:
            _link_logger.warning("couldn't drop links for GMRS mode: %s", error)

    def gmrs_changed(_config: RepeaterConfig) -> None:
        on = service.saved_config.gmrs_mode
        if on and not gmrs_mode[0] and links.client is not None:
            asyncio.get_running_loop().create_task(drop_links())
        gmrs_mode[0] = on

    service.add_config_listener(gmrs_changed)
    auth_settings = auth_settings if auth_settings is not None else auth_settings_from_env(os.environ)
    sessions = SessionStore()
    users = users if users is not None else UserStore()
    users.reserved_username = auth_settings.username if auth_settings else None
    tokens = tokens if tokens is not None else TokenStore()
    agent_access = agent_access if agent_access is not None else AgentAccess()
    audit = audit or AuditLog()
    service.audit_hook = lambda actor, action, detail: audit.record(time.time(), actor, action, detail)
    nets.audit_hook = service.audit_hook
    control_codes = control_codes or ControlCodes(None)
    service.code_checker = control_codes.check
    service.codes_locked = control_codes.locked
    homeassistant = homeassistant or HomeAssistant(service, os.environ.get(HOMEASSISTANT_TOKEN_ENV))
    airport_weather = airport_weather or AirportWeather(service)
    streamer = streamer or Streamer(live_audio.monitor, stream_store, lambda: renderer.sample_rate)
    mailbox = Mailbox(service, mailbox_store, renderer, net_active=lambda: nets.current is not None)
    mailbox.audit_hook = service.audit_hook
    transcriber = transcriber or Transcriber(
        service, [recordings, mailbox_store, monitor_recordings],
        VoskEngine(Path(os.environ.get(VOSK_MODEL_ENV) or DEFAULT_VOSK_MODEL_DIR)),
        os.environ.get(TRANSCRIPTION_KEY_ENV),
    )
    public_listeners: collections.Counter[str] = collections.Counter()
    aprs_stations = aprs_stations or StationStore()
    aprs_receiver = AprsReceiver(aprs_stations, lambda: service.config)
    service.add_config_listener(lambda _config: aprs_receiver.settings_changed())

    def aprs_window_start() -> float:
        return time.time() - service.config.aprs_map_hours * 3600

    service.aprs_summary = lambda: spoken_summary(aprs_stations.stations(aprs_window_start()), service.config, time.time())
    backups = backups or BackupFolder(None)
    updater = updater or Updater(updater_settings_from_env(os.environ))
    update_checker = update_checker or UpdateChecker(repo_from_checkout(REPO_DIR))
    update_channel = update_channel or channel_from_env(os.environ)

    def installed_sha() -> str:
        version = installed_version(REPO_DIR)
        return version["sha"] if version else ""

    auto_updater = AutoUpdater(
        auto_update_store, updater, update_checker, lambda: update_channel, installed_sha, service.idle_seconds
    )
    auto_updater.audit_hook = service.audit_hook
    notifier = notifier or Notifier()
    notifier.station = lambda: service.config.callsign
    notifier.audit_hook = lambda at, action, detail: audit.record(at, "alerts", action, detail)
    service.lockout_hook = lambda locked_out: notifier.post(lockout_alert(locked_out))
    health = HealthMonitor(
        notifier,
        system_probe or SystemProbe(_resolve_data_root(os.environ, _REPO_DATA_DIR)),
        run_marker or RunMarker(None),
        audio_status=lambda: live_audio.status(),
        cm108_path=lambda: getattr(getattr(cm108, "device", None), "path", None),
        update_status=updater.status,
    )
    tx_fan = tx_fan or TxFan(service, cm108, temperature=health.probe.temperature_c)
    backup_sources = BackupSources(service, users, assets_store, recordings, activity_store, audit)
    watchdog = watchdog or Watchdog()
    last_tick: list[Optional[float]] = [None]
    watchdog.watch("controller", lambda: last_tick[0])
    watchdog.watch("audio engine", live_audio.progress)

    def before_watchdog_restart(stalled: str) -> None:
        live_audio.release_ptt()
        health.marker.remember(watchdog=stalled)

    watchdog.on_stall = before_watchdog_restart

    def auth_enabled() -> bool:
        """On once there's an env admin or any stored user -- so adding the
        first user from an open dashboard turns sign-in on."""
        return auth_settings is not None or bool(users)

    def check_login(username: str, password: str) -> Optional[Identity]:
        if auth_settings is not None and verify_credentials(username, password, auth_settings):
            return Identity(username, "admin")
        user = users.authenticate(username, password)
        return Identity(user.username, user.role) if user else None

    def role_of(username: str) -> Optional[Role]:
        if auth_settings is not None and username == auth_settings.username:
            return "admin"
        user = users.get(username)
        return user.role if user else None

    def identify(conn: HTTPConnection) -> Optional[Identity]:
        """Accepts the dashboard's session cookie, an API token
        (`Authorization: Bearer ...`), or HTTP Basic (for curl/scripts).
        Roles are looked up on every request, so a role change or deletion
        applies immediately."""
        if not auth_enabled():
            return _LOCAL
        header = conn.headers.get("Authorization") or ""
        scheme, _, value = header.partition(" ")
        if scheme.lower() == "bearer":
            record = tokens.verify(value.strip())
            role = role_of(record.username) if record else None
            return Identity(record.username, role, record.name) if record and role else None
        credentials = parse_basic_auth_header(header or None)
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
        if identity.role == "listener":
            raise HTTPException(status_code=403, detail="Listener accounts can only use the listening page")
        if identity.role == "viewer" and request.method not in SAFE_METHODS:
            raise HTTPException(status_code=403, detail="Your account is read-only")

    def require_admin(request: Request) -> None:
        if request.state.identity.role != "admin":
            raise HTTPException(status_code=403, detail="Only admins can do that")

    def require_person(request: Request) -> None:
        # A leaked token shouldn't be able to make itself more tokens or accounts.
        if request.state.identity.token:
            raise HTTPException(status_code=403, detail="API tokens can't manage users or tokens")

    def require_transmit_allowed(request: Request) -> None:
        if request.state.identity.token and not agent_access.transmit:
            raise HTTPException(
                status_code=403,
                detail="API tokens can't make the repeater transmit until an admin allows it (Users → Agent access)",
            )

    auth_dependencies = [Depends(require_auth)]
    admin_dependencies = [Depends(require_auth), Depends(require_admin)]
    account_dependencies = [*admin_dependencies, Depends(require_person)]
    transmit_dependencies = [Depends(require_auth), Depends(require_transmit_allowed)]

    async def node_link_loop() -> None:
        assert link_settings is not None
        while True:
            node_id = link_settings.local_node_id or allstar_node.node or ""
            client = NodeLinkClient(
                link_settings.host,
                link_settings.port,
                link_settings.username,
                link_settings.secret,
                node_id,
            )
            try:
                await client.connect()
                _link_logger.info(
                    "connected to app_rpt AMI at %s:%s (node %s)",
                    link_settings.host,
                    link_settings.port,
                    node_id or "not chosen yet",
                )

                def sink(command: SendLinkCommand, _client: NodeLinkClient = client) -> None:
                    asyncio.create_task(_client.send_macro_command(command.node_id, command.command))

                service.set_link_command_sink(sink)
                links.client = client
                async for event in client.events():
                    service.handle_link_event(event)
                    if isinstance(event, LinkStateChanged) and event.linked and service.held_reason("links"):
                        asyncio.create_task(drop_links(event.node_id))  # a node linked to us from outside
            except (OSError, ConnectionError):
                _link_logger.exception("app_rpt AMI connection lost; reconnecting in %ss", LINK_RECONNECT_DELAY_SECONDS)
            finally:
                links.client = None
                await client.close()
            await asyncio.sleep(LINK_RECONNECT_DELAY_SECONDS)

    async def link_schedule_loop() -> None:
        while True:
            await asyncio.sleep(LINK_SCHEDULE_POLL_SECONDS)
            try:
                await link_scheduler.tick(service.wall_now())
            except Exception:
                _link_logger.exception("unexpected error in the link schedule")

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

    async def mailbox_loop() -> None:
        loop = asyncio.get_running_loop()
        while True:
            await asyncio.sleep(MAILBOX_TICK_SECONDS)
            try:
                await loop.run_in_executor(None, mailbox.prune)
                mailbox.remind()
            except Exception:
                logging.getLogger("moreopenrepeater.mailbox").exception("unexpected error in the mailbox")

    async def transcribe_loop() -> None:
        loop = asyncio.get_running_loop()
        while True:
            await asyncio.sleep(TRANSCRIBE_POLL_SECONDS)
            if service.config.transcription_engine == "off":
                continue
            try:
                await loop.run_in_executor(None, transcriber.run_pending)
            except Exception:
                logging.getLogger("moreopenrepeater.transcripts").exception("unexpected error transcribing")

    async def net_loop() -> None:
        while True:
            await asyncio.sleep(NET_POLL_SECONDS)
            try:
                await nets.tick()
            except Exception:
                logging.getLogger("moreopenrepeater.net").exception("unexpected error in net mode")

    async def auto_update_loop() -> None:
        loop = asyncio.get_running_loop()
        while True:
            await asyncio.sleep(AUTO_UPDATE_POLL_SECONDS)
            try:
                await loop.run_in_executor(None, auto_updater.tick)  # runs git and calls GitHub
            except Exception:
                logging.getLogger("moreopenrepeater.updates").exception("unexpected error in automatic updates")

    async def backup_loop() -> None:
        loop = asyncio.get_running_loop()
        while True:
            await asyncio.sleep(BACKUP_CHECK_SECONDS)
            await loop.run_in_executor(None, backups.run_schedule, backup_sources, service.config, time.time())

    async def long_id_loop() -> None:
        while True:
            await asyncio.sleep(LONG_ID_WARM_SECONDS)
            await asyncio.get_running_loop().run_in_executor(None, renderer.warm_long_id, service.config)

    async def activity_flush_loop() -> None:
        while True:
            await asyncio.sleep(ACTIVITY_FLUSH_SECONDS)
            await asyncio.get_running_loop().run_in_executor(None, activity_store.flush)

    async def health_loop() -> None:
        loop = asyncio.get_running_loop()
        for alert in await loop.run_in_executor(None, health.started):
            notifier.post(alert)
        while True:
            await asyncio.sleep(HEALTH_CHECK_SECONDS)
            try:
                for alert in await loop.run_in_executor(None, health.check):
                    notifier.post(alert)
            except Exception:
                logging.getLogger("moreopenrepeater.alerts").exception("unexpected error checking the system's health")

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        tasks: list[asyncio.Task] = []
        # Before the audio engines, so a pin taken from the fan is released before they claim it.
        tx_fan.start()
        if start_background_tick:
            async def tick_loop() -> None:
                last_tick[0] = time.monotonic()
                while True:
                    await asyncio.sleep(TICK_INTERVAL_SECONDS)
                    service.tick()
                    last_tick[0] = time.monotonic()

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
            tasks.append(asyncio.create_task(net_loop()))
            tasks.append(asyncio.create_task(auto_update_loop()))
            tasks.append(asyncio.create_task(streamer.run()))
            tasks.append(asyncio.create_task(mailbox_loop()))
            tasks.append(asyncio.create_task(transcribe_loop()))
            tasks.append(asyncio.create_task(health_loop()))
            tasks.append(asyncio.create_task(activity_flush_loop()))
            tasks.append(asyncio.create_task(long_id_loop()))
            if link_settings is not None:
                tasks.append(asyncio.create_task(node_link_loop()))
                tasks.append(asyncio.create_task(node_directory.run()))
                tasks.append(asyncio.create_task(link_schedule_loop()))
            live_audio.attach(loop)
            monitor_receiver.attach(loop)
            link_radio.attach(loop)
            tasks.append(asyncio.create_task(link_radio.run()))
            await autopatch.start()
            await allstar_node.start()
            sd_notify("READY=1")
            interval = ping_interval()
            if interval is not None:
                watchdog.start(interval)
        gpio.start()
        remote_base.attach(asyncio.get_running_loop())
        async with agent_endpoint.running():
            yield
        if start_background_tick:
            sd_notify("STOPPING=1")
            watchdog.stop()
        for task in tasks:
            task.cancel()
        await gpio.stop()
        await tx_fan.stop()
        await allstar_node.stop()
        await autopatch.stop()
        live_audio.shutdown()
        monitor_receiver.shutdown()
        link_radio.shutdown()
        activity_store.flush()
        if start_background_tick:
            health.stopping()

    app = FastAPI(title="moreopenrepeater API", lifespan=lifespan)
    app.state.service = service
    agent_endpoint = AgentEndpoint(build_server(lambda: app), identify)
    app.router.routes.append(Route(MCP_PATH, agent_endpoint))

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
            actor = getattr(state, "audit_actor", None) or (identity and identity.actor)
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

    session_routes.register(
        app,
        identify=identify,
        auth_enabled=auth_enabled,
        check_login=check_login,
        sessions=sessions,
        session_response=session_response,
        local_identity=_LOCAL,
        login_failure_delay=LOGIN_FAILURE_DELAY_SECONDS,
    )

    core_routes.register(
        app,
        service=service,
        run_mixer=run_mixer,
        render_and_queue=render_and_queue,
        status_response=_status_response,
        config_response=_config_response,
        auth_dependencies=auth_dependencies,
        transmit_dependencies=transmit_dependencies,
    )

    simulate_routes.register(
        app,
        service=service,
        transmit_dependencies=transmit_dependencies,
        status_response=_status_response,
    )

    def actor_of(request: Request) -> str:
        return request.state.identity.username or "local"

    nets_routes.register(
        app,
        nets=nets,
        actor_of=actor_of,
        auth_dependencies=auth_dependencies,
        transmit_dependencies=transmit_dependencies,
    )

    programming_routes.register(
        app,
        service=service,
        assets_store=assets_store,
        zone_shapes=zone_shapes,
        render_and_queue=render_and_queue,
        check_weather=check_weather,
        status_response=_status_response,
        auth_dependencies=auth_dependencies,
        transmit_dependencies=transmit_dependencies,
    )

    activity_routes.register(
        app,
        service=service,
        activity_store=activity_store,
        snapshot_to_import=_snapshot_to_import,
        auth_dependencies=auth_dependencies,
    )

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

    admin_ops_routes.register(
        app,
        service=service,
        backups=backups,
        backup_sources=backup_sources,
        audit=audit,
        updater=updater,
        update_checker=update_checker,
        update_channel=update_channel,
        auto_updater=auto_updater,
        notifier=notifier,
        health=health,
        restore_backup_file=restore_backup_file,
        repo_dir=REPO_DIR,
        admin_dependencies=admin_dependencies,
    )

    audio_tools_routes.register(
        app,
        service=service,
        renderer=renderer,
        live_audio=live_audio,
        audio_devices=audio_devices,
        serial_ports=serial_ports,
        apply_overrides=_apply_overrides,
        auth_dependencies=auth_dependencies,
    )

    linking_routes.register(
        app,
        service=service,
        autopatch=autopatch,
        allstar_node=allstar_node,
        links=links,
        link_scheduler=link_scheduler,
        gpio=gpio,
        tx_fan=tx_fan,
        monitor_receiver=monitor_receiver,
        link_radio=link_radio,
        remote_base=remote_base,
        auth_dependencies=auth_dependencies,
        admin_dependencies=admin_dependencies,
        transmit_dependencies=transmit_dependencies,
    )

    autopatch_routes.register(
        app,
        service=service,
        autopatch=autopatch,
        sip_trunk=sip_trunk,
        auth_dependencies=auth_dependencies,
        admin_dependencies=admin_dependencies,
        transmit_dependencies=transmit_dependencies,
    )

    media_routes.register(
        app,
        service=service,
        assets_store=assets_store,
        recordings=recordings,
        monitor_recordings=monitor_recordings,
        transcriber=transcriber,
        auth_dependencies=auth_dependencies,
    )

    mailbox_routes.register(
        app,
        mailbox_store=mailbox_store,
        admin_dependencies=admin_dependencies,
    )

    site_routes.register(
        app,
        service=service,
        log_path=log_path,
        aprs_stations=aprs_stations,
        aprs_receiver=aprs_receiver,
        aprs_window_start=aprs_window_start,
        homeassistant=homeassistant,
        airport_weather=airport_weather,
        streamer=streamer,
        audit=audit,
        audit_response=_audit_response,
        actor_of=actor_of,
        auth_dependencies=auth_dependencies,
        admin_dependencies=admin_dependencies,
    )

    accounts_routes.register(
        app,
        users=users,
        tokens=tokens,
        agent_access=agent_access,
        agent_endpoint=agent_endpoint,
        auth_settings=auth_settings,
        control_codes=control_codes,
        role_of=role_of,
        account_dependencies=account_dependencies,
    )

    control_codes_routes.register(
        app,
        service=service,
        control_codes=control_codes,
        auth_dependencies=auth_dependencies,
        admin_dependencies=admin_dependencies,
    )

    realtime_routes.register(
        app,
        service=service,
        nets=nets,
        live_audio=live_audio,
        monitor_receiver=monitor_receiver,
        renderer=renderer,
        public_listeners=public_listeners,
        identify=identify,
        auth_enabled=auth_enabled,
        status_response=_status_response,
        web_dir=WEB_DIR,
    )

    return app


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
