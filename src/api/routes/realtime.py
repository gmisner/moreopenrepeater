"""WebSockets, public listen status, and dashboard HTML pages."""
from __future__ import annotations

import asyncio
import collections
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlsplit

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

    def websocket_allowed(websocket: WebSocket) -> bool:
        if not auth_enabled():
            return True
        if not same_origin(websocket):
            return False
        identity = identify(websocket)
        return identity is not None and identity.role != "listener"

    @app.websocket("/ws/audio")
    async def ws_audio(websocket: WebSocket, source: ListenSource = "tx") -> None:
        """Binary frames of 16-bit little-endian mono PCM at the processing rate."""
        if not websocket_allowed(websocket):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        monitor: AudioMonitor = monitor_receiver.monitor if source == "monitor" else live_audio.monitor
        queue = monitor.subscribe("rx" if source == "monitor" else source)

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
            monitor.unsubscribe(queue)

    def client_address(websocket: WebSocket) -> str:
        forwarded = websocket.headers.get("x-forwarded-for", "").split(",")[0].strip()
        return forwarded or (websocket.client.host if websocket.client else "")

    @app.websocket("/ws/public/audio")
    async def ws_public_audio(websocket: WebSocket) -> None:
        """What's on the air, for /listen. Capped in total; listeners who
        aren't signed in are also capped per address."""
        config = service.saved_config
        identity = listen_identity(websocket) if same_origin(websocket) else None
        signed_in = identity is not None or not auth_enabled()

        def allowed() -> bool:
            config = service.saved_config
            mode = config.public_page_mode
            return config.public_page_audio and (mode == "anyone" or (mode == "signed_in" and signed_in))

        if not allowed():
            await websocket.close(code=1008)
            return
        key = f"user:{identity.username}" if identity else client_address(websocket)
        full = sum(public_listeners.values()) >= config.public_page_max_listeners
        if full or (identity is None and public_listeners[key] >= PUBLIC_LISTENERS_PER_ADDRESS):
            await websocket.close(code=1013)
            return
        public_listeners[key] += 1
        try:
            await websocket.accept()
            queue = live_audio.monitor.subscribe("tx")

            async def send_frames() -> None:
                await websocket.send_json({"sample_rate": renderer.sample_rate})
                while True:
                    await websocket.send_bytes(await queue.get())
                    if not allowed():
                        await websocket.close(code=1008)
                        return

            sender = asyncio.create_task(send_frames())
            try:
                while (await websocket.receive())["type"] != "websocket.disconnect":
                    pass
            finally:
                sender.cancel()
                live_audio.monitor.unsubscribe(queue)
        finally:
            public_listeners[key] -= 1
            if public_listeners[key] <= 0:
                del public_listeners[key]

    @app.websocket("/ws/status")
    async def ws_status(websocket: WebSocket) -> None:
        if not websocket_allowed(websocket):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        queue = service.subscribe()
        try:
            await websocket.send_json(status_response(service.snapshot()).model_dump())
            while True:
                snapshot = await queue.get()
                await websocket.send_json(status_response(snapshot).model_dump())
        except WebSocketDisconnect:
            pass
        finally:
            service.unsubscribe(queue)

    if web_dir.is_dir():
        # Prefer the Mantine build when web/dist/index.html and web/dist/.ready
        # both exist (ADR 0001). Until cutover, the vanilla dashboard stays.
        # Pi installs never run npm — dist is committed from CI / developers.
        dist_dir = web_dir / "dist"
        spa = (dist_dir / "index.html").is_file() and (dist_dir / ".ready").is_file()
        static_root = dist_dir if spa else web_dir

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
            return page("index.html" if spa else "listen.html")

        @app.get("/login", response_model=None)
        def login_page(request: Request) -> Response:
            if not auth_enabled() or is_signed_in(request):
                return RedirectResponse("/", status_code=303)
            return page("index.html" if spa else "login.html")

        # SPA deep links (e.g. /dashboard) must return index.html.
        if spa:

            def spa_route() -> Response:
                return page("index.html")

            for spa_path in (
                "dashboard",
                "activity",
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
            ):
                app.add_api_route(f"/{spa_path}", spa_route, methods=["GET"], response_model=None)

        # Left unauthenticated: the JS/CSS hold no secrets (every piece of
        # repeater data comes from the authenticated API), and a Mount is a
        # raw ASGI sub-application that can't take route-level
        # `dependencies=` anyway.
        app.mount("/", StaticFiles(directory=static_root), name="web")
