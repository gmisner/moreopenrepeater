"""WebSockets, public listen status, and dashboard HTML pages."""
from __future__ import annotations

import asyncio
import collections
from contextlib import suppress
from pathlib import Path
from typing import AsyncIterator, Callable, Optional
from urllib.parse import urlsplit

from anyio import CancelScope
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.requests import HTTPConnection

from ..live_audio import LiveAudio
from ..models import ListenSource, PublicStatus, StatusResponse
from ..monitor import AudioMonitor
from ..monitor_receiver import MonitorReceiverService
from ..net import NetMode
from ..service import RepeaterService
from playout.renderer import ClipRenderer

PUBLIC_LISTENERS_PER_ADDRESS = 3
ACCESS_CHECK_SECONDS = 0.25
SEND_TIMEOUT_SECONDS = 5.0


async def stream(
    websocket: WebSocket,
    frames: AsyncIterator[dict | bytes],
    allowed: Callable[[], bool],
) -> None:
    async def send_frames() -> int:
        async for frame in frames:
            if not allowed():
                return 1008
            try:
                async with asyncio.timeout(SEND_TIMEOUT_SECONDS):
                    if isinstance(frame, bytes):
                        await websocket.send_bytes(frame)
                    else:
                        await websocket.send_json(frame)
            except TimeoutError:
                return 1013
        return 1000

    async def watch_disconnect() -> None:
        while (await websocket.receive())["type"] != "websocket.disconnect":
            pass

    async def watch_access() -> int:
        # Audio can remain silent indefinitely, including when the engine is off.
        while allowed():
            await asyncio.sleep(ACCESS_CHECK_SECONDS)
        return 1008

    try:
        async with asyncio.timeout(SEND_TIMEOUT_SECONDS):
            await websocket.accept()
    except (TimeoutError, WebSocketDisconnect):
        return
    receiver = asyncio.create_task(watch_disconnect())
    access = asyncio.create_task(watch_access())
    sender = asyncio.create_task(send_frames())
    tasks = (receiver, access, sender)
    try:
        done, _pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        code = None if receiver in done else (access if access in done else sender).result()
    except WebSocketDisconnect:
        code = None
    finally:
        for task in tasks:
            task.cancel()
        # Let task cleanup finish even when the ASGI caller is cancelling.
        with CancelScope(shield=True):
            await asyncio.gather(*tasks, return_exceptions=True)

    if code is not None:
        with suppress(TimeoutError, WebSocketDisconnect):
            async with asyncio.timeout(SEND_TIMEOUT_SECONDS):
                await websocket.close(code=code)


def register(
    app: FastAPI,
    *,
    service: RepeaterService,
    nets: NetMode,
    live_audio: LiveAudio,
    monitor_receiver: MonitorReceiverService,
    renderer: ClipRenderer,
    public_listeners: collections.Counter,
    identify: Callable,
    bind_stream_identity: Callable,
    auth_enabled: Callable[[], bool],
    status_response: Callable[..., StatusResponse],
    web_dir: Path,
) -> None:
    def public_page_mode() -> str:
        return service.saved_config.public_page_mode

    def listen_identity(conn: HTTPConnection) -> Optional[object]:
        """Who's signed in, for the listening page; None if nobody is (or
        sign-in is off, where everyone would count as the admin)."""
        return identify(conn) if auth_enabled() else None

    def may_open_listen_page(conn: HTTPConnection) -> bool:
        mode = public_page_mode()
        return mode == "anyone" or (mode == "signed_in" and identify(conn) is not None)

    @app.get("/api/public/status", response_model=PublicStatus)
    def public_status(request: Request) -> PublicStatus:
        """What /listen shows: open to anyone or only to signed-in accounts,
        depending on the listening page setting."""
        if public_page_mode() == "off":
            raise HTTPException(status_code=404, detail="Not found")
        if not may_open_listen_page(request):
            raise HTTPException(status_code=401, detail="Not authenticated")
        identity = listen_identity(request)
        config = service.saved_config
        snapshot = service.snapshot()
        return PublicStatus(
            callsign=config.callsign,
            text=config.public_page_text,
            on_air=snapshot.ptt_active,
            receiving=snapshot.cos_active,
            net=nets.current["name"] if nets.current else None,
            audio=config.public_page_audio,
            listeners=sum(public_listeners.values()),
            max_listeners=config.public_page_max_listeners,
            username=identity.username if identity else None,
        )

    def same_origin(websocket: WebSocket) -> bool:
        # WebSocket handshakes aren't subject to CORS, so a page on another
        # origin could otherwise open one with the user's session cookie.
        origin = websocket.headers.get("origin")
        return not origin or urlsplit(origin).netloc == websocket.headers.get("host")

    async def private_access(websocket: WebSocket) -> Callable[[], bool]:
        same_site = same_origin(websocket)
        if auth_enabled() and not same_site:
            return lambda: False
        identity_now = await bind_stream_identity(websocket)
        identity = identity_now()

        def allowed() -> bool:
            return (
                (not auth_enabled() or same_site)
                and identity is not None
                and identity.role != "listener"
                and identity_now() == identity
            )

        return allowed

    async def audio_frames(queue: asyncio.Queue, **metadata: object) -> AsyncIterator[dict | bytes]:
        yield {"sample_rate": renderer.sample_rate, **metadata}
        while True:
            frame = await queue.get()
            queue.task_done()
            yield frame

    @app.websocket("/ws/audio")
    async def ws_audio(websocket: WebSocket, source: ListenSource = "tx") -> None:
        """Binary frames of 16-bit little-endian mono PCM at the processing rate."""
        allowed = await private_access(websocket)
        if not allowed():
            await websocket.close(code=1008)
            return
        monitor: AudioMonitor = monitor_receiver.monitor if source == "monitor" else live_audio.monitor
        queue = monitor.subscribe("rx" if source == "monitor" else source)
        try:
            await stream(websocket, audio_frames(queue, source=source), allowed)
        finally:
            monitor.unsubscribe(queue)

    @app.websocket("/ws/public/audio")
    async def ws_public_audio(websocket: WebSocket) -> None:
        """What's on the air, for /listen. Capped in total; listeners who
        aren't signed in are also capped per address."""
        if same_origin(websocket):
            identity_now = await bind_stream_identity(websocket)
        else:
            identity_now = lambda: None
        identity = identity_now() if auth_enabled() else None

        def allowed() -> bool:
            config = service.saved_config
            if identity is not None and identity_now() != identity:
                return False
            signed_in = identity is not None or not auth_enabled()
            mode = config.public_page_mode
            return config.public_page_audio and (mode == "anyone" or (mode == "signed_in" and signed_in))

        if not allowed():
            await websocket.close(code=1008)
            return
        # The ASGI server resolves the client address through configured trusted
        # proxies. Reading forwarding headers here would let callers choose it.
        address = websocket.client.host if websocket.client else ""
        key = f"user:{identity.username}" if identity else address
        full = sum(public_listeners.values()) >= service.saved_config.public_page_max_listeners
        if full or (identity is None and public_listeners[key] >= PUBLIC_LISTENERS_PER_ADDRESS):
            await websocket.close(code=1013)
            return
        public_listeners[key] += 1
        try:
            queue = live_audio.monitor.subscribe("tx")
            try:
                await stream(websocket, audio_frames(queue), allowed)
            finally:
                live_audio.monitor.unsubscribe(queue)
        finally:
            public_listeners[key] -= 1
            if public_listeners[key] <= 0:
                del public_listeners[key]

    @app.websocket("/ws/status")
    async def ws_status(websocket: WebSocket) -> None:
        allowed = await private_access(websocket)
        if not allowed():
            await websocket.close(code=1008)
            return
        queue = service.subscribe()
        queue.put_nowait(service.snapshot())

        async def status_frames() -> AsyncIterator[dict]:
            while True:
                snapshot = await queue.get()
                queue.task_done()
                yield status_response(snapshot).model_dump()

        try:
            await stream(websocket, status_frames(), allowed)
        finally:
            service.unsubscribe(queue)

    # Mantine-only (ADR 0001): serve web/dist. Pi installs never run npm —
    # dist is committed from CI / developers. No vanilla HTML/JS fallback.
    dist_dir = web_dir / "dist"
    index_html = dist_dir / "index.html"
    if not index_html.is_file():
        raise RuntimeError(
            f"Mantine dashboard missing: expected {index_html}. "
            "Build with `npm run build` in frontend/ and commit web/dist/."
        )
    static_root = dist_dir

    def is_signed_in(request: Request) -> bool:
        return identify(request) is not None

    def page(name: str) -> FileResponse:
        return FileResponse(static_root / name)

    @app.get("/", response_model=None)
    def index(request: Request) -> Response:
        identity = identify(request)
        if identity is None:
            return RedirectResponse("/login", status_code=303)
        if identity.role == "listener":
            return RedirectResponse("/listen", status_code=303)
        return page("index.html")

    @app.get("/listen", response_model=None)
    def listen_page(request: Request) -> Response:
        if public_page_mode() == "off":
            raise HTTPException(status_code=404, detail="Not found")
        if not may_open_listen_page(request):
            return RedirectResponse("/login?next=/listen", status_code=303)
        return page("index.html")

    @app.get("/login", response_model=None)
    def login_page(request: Request) -> Response:
        if not auth_enabled() or is_signed_in(request):
            return RedirectResponse("/", status_code=303)
        return page("index.html")

    # SPA deep links (e.g. /dashboard) must return index.html.
    def spa_route() -> Response:
        return page("index.html")

    for spa_path in (
        "dashboard",
        "configs",
        "activity",
        "recordings",
        "timing",
        "identification",
        "audio",
        "macros",
        "net",
        "mailbox",
        "announcements",
        "weather",
        "autopatch",
        "allstar",
        "aprs",
        "map",
        "public",
        "simulator",
        "logs",
        "backup",
        "users",
        "audit",
        "alerts",
        "updates",
        "remote",
        "sites",
        "sensors",
    ):
        app.add_api_route(f"/{spa_path}", spa_route, methods=["GET"], response_model=None)

    # Left unauthenticated: the JS/CSS hold no secrets (every piece of
    # repeater data comes from the authenticated API), and a Mount is a
    # raw ASGI sub-application that can't take route-level
    # `dependencies=` anyway.
    app.mount("/", StaticFiles(directory=static_root), name="web")
