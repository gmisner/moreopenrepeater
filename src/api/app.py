"""FastAPI app: REST + WebSocket status API, plus the static dashboard."""
from __future__ import annotations

import asyncio
import collections
import dataclasses
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import NamedTuple, Optional
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from starlette.requests import HTTPConnection

from controller.events import SendLinkCommand
from controller.macros import Macro
from controller.state_machine import RepeaterConfig
from link.aprs_client import APRSClient, format_position_report, format_status_report
from link.node_link import NodeLinkClient
from playout.renderer import ClipRenderer, UnknownClipError
from playout.tts import TTSError, detect_tts
from playout.wav import encode_wav

from .assets import AssetKind, AudioAssetStore
from .auth import (
    SESSION_COOKIE_NAME,
    SESSION_TTL_SECONDS,
    AuthSettings,
    SessionStore,
    auth_settings_from_env,
    credentials_match,
    verify_credentials,
)
from .logging_config import configure_logging
from .models import (
    AssetResponse,
    AudioPreviewRequest,
    ConfigResponse,
    ConfigUpdateRequest,
    LoginRequest,
    MacroCreateRequest,
    MacroResponse,
    SessionResponse,
    SimulateCOSRequest,
    SimulateCTCSSRequest,
    SimulateDTMFRequest,
    SimulateRemoteKeyedRequest,
    SnapshotImportRequest,
    SnapshotModel,
    StatusResponse,
    TTSInfoResponse,
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
DEFAULT_LOG_PATH = _resolve_log_path(os.environ, _REPO_DATA_DIR)
TICK_INTERVAL_SECONDS = 0.05
APRS_DISABLED_POLL_SECONDS = 5.0
LINK_RECONNECT_DELAY_SECONDS = 5.0
LOGIN_FAILURE_DELAY_SECONDS = 1.0
MAX_PREVIEW_CLIP_LENGTH = 2000

_aprs_logger = logging.getLogger("moreopenrepeater.aprs")
_link_logger = logging.getLogger("moreopenrepeater.link")
_auth_logger = logging.getLogger("moreopenrepeater.auth")


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


def _validated_config(values: dict) -> RepeaterConfig:
    """Type-check a full config dict through the API model, turning bad
    values into the same 422 FastAPI gives for a bad request body."""
    try:
        validated = ConfigResponse.model_validate({k: v for k, v in values.items() if k in ConfigResponse.model_fields})
    except ValidationError as error:
        raise RequestValidationError(error.errors()) from error
    return RepeaterConfig(**validated.model_dump())


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
) -> FastAPI:
    """`state_store` defaults to None (in-memory only) so tests never touch
    the real `data/state.json`; the module-level `app` below opts in."""
    assets_store = assets_store or AudioAssetStore(DEFAULT_DATA_DIR)
    renderer = renderer or ClipRenderer(assets_store.path_for, tts=detect_tts())
    service = service or RepeaterService(state_store=state_store)
    if service.renderer is None:
        service.renderer = renderer
    log_path = log_path or DEFAULT_LOG_PATH
    link_settings = link_settings if link_settings is not None else link_settings_from_env()
    auth_settings = auth_settings if auth_settings is not None else auth_settings_from_env(os.environ)
    configure_logging(log_path)
    sessions = SessionStore()

    def authenticated_username(conn: HTTPConnection) -> Optional[str]:
        """Accepts either the dashboard's session cookie or a Basic
        `Authorization` header (for curl/scripts)."""
        assert auth_settings is not None
        username = sessions.username_for(conn.cookies.get(SESSION_COOKIE_NAME))
        if username is not None:
            return username
        if credentials_match(conn.headers.get("Authorization"), auth_settings):
            return auth_settings.username
        return None

    def require_auth(request: Request) -> None:
        # No WWW-Authenticate header: it would make browsers pop their
        # native Basic Auth dialog over the dashboard's own login page
        # whenever a fetch() hits an expired session.
        if authenticated_username(request) is None:
            raise HTTPException(status_code=401, detail="Not authenticated")

    auth_dependencies = [Depends(require_auth)] if auth_settings is not None else []

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
            if config.aprs_lat is not None and config.aprs_lon is not None:
                packet = format_position_report(config.aprs_lat, config.aprs_lon, config.aprs_comment)
            else:
                packet = format_status_report(config.aprs_comment)
            await client.send_packet(packet)
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

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        tick_task: Optional[asyncio.Task] = None
        aprs_task: Optional[asyncio.Task] = None
        link_task: Optional[asyncio.Task] = None
        if start_background_tick:
            async def tick_loop() -> None:
                while True:
                    await asyncio.sleep(TICK_INTERVAL_SECONDS)
                    service.tick()

            tick_task = asyncio.create_task(tick_loop())
            aprs_task = asyncio.create_task(aprs_beacon_loop())
            loop = asyncio.get_running_loop()
            loop.run_in_executor(None, renderer.warm, service.config)
            service.add_config_listener(lambda config: loop.run_in_executor(None, renderer.warm, config))
            if link_settings is not None:
                link_task = asyncio.create_task(node_link_loop())
        yield
        if tick_task is not None:
            tick_task.cancel()
        if aprs_task is not None:
            aprs_task.cancel()
        if link_task is not None:
            link_task.cancel()

    app = FastAPI(title="moreopenrepeater API", lifespan=lifespan)
    app.state.service = service

    @app.middleware("http")
    async def revalidate_static_files(request: Request, call_next):
        # Without this, browsers heuristically cache app.js/style.css and
        # keep running the old dashboard after an upgrade. "no-cache" still
        # allows caching -- it just forces a cheap ETag revalidation (304).
        response = await call_next(request)
        if not request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-cache")
        return response

    @app.get("/api/session", response_model=SessionResponse)
    def get_session(request: Request) -> SessionResponse:
        if auth_settings is None:
            return SessionResponse(auth_required=False, authenticated=True, username=None)
        username = authenticated_username(request)
        return SessionResponse(auth_required=True, authenticated=username is not None, username=username)

    @app.post("/api/login", response_model=SessionResponse)
    async def login(body: LoginRequest, request: Request, response: Response) -> SessionResponse:
        if auth_settings is None:
            return SessionResponse(auth_required=False, authenticated=True, username=None)
        if not verify_credentials(body.username, body.password, auth_settings):
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
        return SessionResponse(auth_required=True, authenticated=True, username=body.username)

    @app.post("/api/logout", response_model=SessionResponse)
    async def logout(request: Request, response: Response) -> SessionResponse:
        sessions.revoke(request.cookies.get(SESSION_COOKIE_NAME))
        response.delete_cookie(SESSION_COOKIE_NAME, httponly=True, samesite="strict")
        return SessionResponse(auth_required=auth_settings is not None, authenticated=auth_settings is None, username=None)

    @app.get("/api/status", response_model=StatusResponse, dependencies=auth_dependencies)
    def get_status() -> StatusResponse:
        return _status_response(service.snapshot())

    @app.get("/api/config", response_model=ConfigResponse, dependencies=auth_dependencies)
    def get_config() -> ConfigResponse:
        return _config_response(service)

    @app.put("/api/config", response_model=ConfigResponse, dependencies=auth_dependencies)
    async def put_config(update: ConfigUpdateRequest) -> ConfigResponse:
        clear_fields = {name for name in ConfigUpdateRequest.model_fields if name.startswith("clear_")}
        overrides = update.model_dump(exclude=clear_fields, exclude_none=True)
        for clear_field in clear_fields:
            if getattr(update, clear_field):
                overrides[clear_field.removeprefix("clear_")] = None
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
        macro = Macro(pattern=body.pattern, description=body.description, command=body.command, node_id=body.node_id)
        return [_macro_response(m) for m in service.add_macro(macro)]

    @app.delete("/api/macros/{pattern}", response_model=list[MacroResponse], dependencies=auth_dependencies)
    async def delete_macro(pattern: str) -> list[MacroResponse]:
        if pattern not in {m.pattern for m in service.list_macros()}:
            raise HTTPException(status_code=404, detail=f"No macro with pattern {pattern!r}")
        return [_macro_response(m) for m in service.delete_macro(pattern)]

    @app.get("/api/snapshot", response_model=SnapshotModel, dependencies=auth_dependencies)
    def get_snapshot() -> SnapshotModel:
        return SnapshotModel(**service.export_snapshot())

    @app.post("/api/snapshot", response_model=SnapshotModel, dependencies=auth_dependencies)
    async def post_snapshot(snapshot: SnapshotImportRequest) -> SnapshotModel:
        config = _validated_config({**dataclasses.asdict(RepeaterConfig()), **snapshot.config})
        service.import_snapshot(
            {"config": dataclasses.asdict(config), "macros": [m.model_dump() for m in snapshot.macros]}
        )
        return SnapshotModel(**service.export_snapshot())

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

    @app.get("/api/logs", response_model=list[str], dependencies=auth_dependencies)
    def get_logs(lines: int = 200) -> list[str]:
        if not log_path.exists():
            return []
        with log_path.open() as f:
            return [line.rstrip("\n") for line in collections.deque(f, maxlen=lines)]

    @app.websocket("/ws/status")
    async def ws_status(websocket: WebSocket) -> None:
        # WebSocket handshakes aren't subject to CORS, so a page on another
        # origin could otherwise open this with the user's session cookie.
        origin = websocket.headers.get("origin")
        if auth_settings is not None and origin and urlsplit(origin).netloc != websocket.headers.get("host"):
            await websocket.close(code=1008)
            return
        if auth_settings is not None and authenticated_username(websocket) is None:
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
            return auth_settings is None or authenticated_username(request) is not None

        @app.get("/", response_model=None)
        def index(request: Request) -> Response:
            if not is_signed_in(request):
                return RedirectResponse("/login", status_code=303)
            return FileResponse(WEB_DIR / "index.html")

        @app.get("/login", response_model=None)
        def login_page(request: Request) -> Response:
            if auth_settings is None or is_signed_in(request):
                return RedirectResponse("/", status_code=303)
            return FileResponse(WEB_DIR / "login.html")

        # Left unauthenticated: the JS/CSS hold no secrets (every piece of
        # repeater data comes from the authenticated API), and a Mount is a
        # raw ASGI sub-application that can't take route-level
        # `dependencies=` anyway.
        app.mount("/", StaticFiles(directory=WEB_DIR), name="web")

    return app


app = create_app(state_store=StateStore(DEFAULT_STATE_PATH))


def main() -> None:
    import uvicorn

    host = os.environ.get("MOREOPENREPEATER_HOST", "0.0.0.0")
    port = int(os.environ.get("MOREOPENREPEATER_PORT", "8000"))
    uvicorn.run("api.app:app", host=host, port=port)


if __name__ == "__main__":
    main()
