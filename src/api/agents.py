"""The MCP server at /mcp, for AI agents.

Each tool is a thin wrapper around the dashboard's own HTTP API, called
in-process with the agent's credentials. So an agent gets exactly what its
API token's user could do with curl: the same validation, role checks and
audit log entries, with nothing duplicated here.

Anything that makes the repeater transmit is also refused to API tokens
unless an admin turns on `transmit` (see `AgentAccess`). Under Part 97 the
control operator answers for what goes out, so that's a deliberate choice,
not a default.
"""
from __future__ import annotations

import contextvars
import json
import logging
import re
import threading
from contextlib import asynccontextmanager
from typing import Any, Callable, Optional

from starlette.requests import HTTPConnection
from starlette.types import ASGIApp, Receive, Scope, Send

from .persistence import StateStore

_logger = logging.getLogger("moreopenrepeater.agents")

try:
    import httpx2
    from mcp.server import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError
    from mcp.server.transport_security import TransportSecuritySettings
except ImportError:  # pragma: no cover - mcp is a core dependency; this keeps a broken install serving the dashboard
    MCPServer = None

MCP_PATH = "/mcp"
DTMF_DIGITS = re.compile(r"^[0-9*#A-D]{1,16}$")
INSTRUCTIONS = """\
Controls a moreopenrepeater amateur radio repeater controller.

Start with get_status and get_audio to see what the repeater is doing: its
state (idle, receiving, hang time...), whether a carrier is detected, the
receive audio level in dBFS and whether the transmitter is keyed. get_settings
lists every setting with its current value; update_settings changes them.

Tools that make the repeater transmit (send_id, dial_dtmf,
play_announcement) only work if an admin has allowed agents to transmit.
Everything you change is recorded in the repeater's audit log under your
API token's name.
"""

_authorization: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("mcp_authorization", default=None)


class AgentAccess:
    """Admin-only switches for agents, kept out of the repeater settings so
    no settings change, snapshot import or backup restore can flip them."""

    def __init__(self, store: Optional[StateStore] = None) -> None:
        self._store = store
        self._lock = threading.Lock()
        data = (store.load() if store else None) or {}
        self.transmit = bool(data.get("transmit", False))

    def set_transmit(self, allowed: bool) -> None:
        with self._lock:
            self.transmit = allowed
            if self._store is not None:
                self._store.save({"transmit": allowed})


def _error_detail(response) -> str:
    try:
        detail = response.json().get("detail")
    except (ValueError, AttributeError):
        detail = None
    if isinstance(detail, list):  # FastAPI validation errors
        return "; ".join(f"{'.'.join(str(p) for p in d.get('loc', [])[1:])}: {d.get('msg')}" for d in detail)
    return str(detail or response.text or response.reason_phrase)


def build_server(app_getter: Callable[[], ASGIApp]) -> Optional["MCPServer"]:
    if MCPServer is None:
        return None
    server = MCPServer("moreopenrepeater", instructions=INSTRUCTIONS)

    async def api(method: str, path: str, **kwargs: Any) -> Any:
        headers = {"Authorization": auth} if (auth := _authorization.get()) else {}
        transport = httpx2.ASGITransport(app=app_getter())
        async with httpx2.AsyncClient(transport=transport, base_url="http://localhost") as client:
            response = await client.request(method, path, headers=headers, **kwargs)
        if response.status_code >= 400:
            raise ToolError(f"{response.status_code}: {_error_detail(response)}")
        return response.json() if response.content else None

    @server.tool()
    async def get_status() -> dict:
        """The repeater's live state: idle/receiving/hang time/ID..., whether a local
        carrier or a linked station is keyed, PTT, lockout, net and link status."""
        return await api("GET", "/api/status")

    @server.tool()
    async def get_audio() -> dict:
        """The live audio engine: whether it's running (and any error), the sound devices,
        the receive level in dBFS, carrier detect (cos_open), the CTCSS tone heard, and
        whether it's transmitting. Poll this while someone keys up to set the VOX level."""
        return await api("GET", "/api/audio/engine")

    @server.tool()
    async def list_audio_devices() -> list:
        """Sound devices the controller can see, with their input and output channel counts."""
        return await api("GET", "/api/audio/devices")

    @server.tool()
    async def get_settings() -> dict:
        """Every repeater setting and its current value. The field names are what
        update_settings takes."""
        return await api("GET", "/api/config")

    @server.tool()
    async def update_settings(changes: dict[str, Any]) -> dict:
        """Change settings, e.g. {"vox_threshold_db": -50} or {"hang_time": 2.5}. Only the
        fields given change. To blank an optional field, send {"clear_<field>": true}.
        Values are validated as they are from the dashboard; returns the new settings."""
        return await api("PUT", "/api/config", json=changes)

    @server.tool()
    async def recent_receptions(limit: int = 20) -> list:
        """The most recent local transmissions the repeater received: start time, length
        in seconds, and whether they timed out."""
        return await api("GET", "/api/activity/transmissions", params={"limit": max(1, min(limit, 500))})

    @server.tool()
    async def activity_summary(days: int = 7) -> dict:
        """Airtime, kerchunks, timeouts and IDs over the last `days` days."""
        return await api("GET", "/api/activity/summary", params={"days": max(1, min(days, 365))})

    @server.tool()
    async def recent_logs(lines: int = 100) -> list:
        """The last lines of the controller's log: audio engine starts and errors, settings
        changes, link events, warnings."""
        return await api("GET", "/api/logs", params={"lines": max(1, min(lines, 1000))})

    @server.tool()
    async def list_macros() -> list:
        """The DTMF macros: the digits to dial, what each does, and its description."""
        return await api("GET", "/api/macros")

    @server.tool()
    async def list_announcements() -> list:
        """Scheduled announcements, with their ids for play_announcement."""
        return await api("GET", "/api/announcements")

    @server.tool()
    async def send_id() -> dict:
        """Transmit the station ID now. Needs agents to be allowed to transmit."""
        return await api("POST", "/api/audio/test-id")

    @server.tool()
    async def dial_dtmf(digits: str) -> dict:
        """Act as if `digits` (0-9, *, #, A-D) were keyed over the air, which runs any
        matching macro. Most macros transmit, so this needs agents to be allowed to transmit."""
        if not DTMF_DIGITS.match(digits):
            raise ToolError("Digits must be 1 to 16 of 0-9, *, # and A-D")
        status = None
        for digit in digits:
            status = await api("POST", "/api/simulate/dtmf", json={"digit": digit})
        return status

    @server.tool()
    async def play_announcement(announcement_id: str) -> dict:
        """Transmit an announcement now. Needs agents to be allowed to transmit."""
        return await api("POST", f"/api/announcements/{announcement_id}/play")

    return server


class AgentEndpoint:
    """The ASGI app at /mcp: checks who's calling, then hands the request to
    the MCP server with their credentials in context for the tools."""

    def __init__(self, server: Optional["MCPServer"], identify: Callable[[HTTPConnection], Any]) -> None:
        self._server = server
        self._identify = identify
        self._app: Optional[ASGIApp] = None
        if server is not None:
            # Requests are authenticated here, and browsers are refused below,
            # so the SDK's localhost-only Host check would only get in the way.
            self._app = server.streamable_http_app(
                streamable_http_path=MCP_PATH,
                stateless_http=True,
                json_response=True,
                transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
            )

    @property
    def available(self) -> bool:
        return self._app is not None

    @asynccontextmanager
    async def running(self):
        if self._server is None:
            yield
            return
        async with self._server.session_manager.run():
            yield

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        conn = HTTPConnection(scope)
        if self._app is None:
            await _reply(send, 503, {"detail": "MCP support isn't installed (pip install mcp)"})
            return
        if conn.headers.get("origin"):
            # MCP clients aren't web pages; this keeps a page in a signed-in
            # browser from driving the repeater with the session cookie.
            await _reply(send, 403, {"detail": "Browsers can't use the MCP endpoint"})
            return
        identity = self._identify(conn)
        if identity is None:
            await _reply(send, 401, {"detail": "Not authenticated: send an API token as Authorization: Bearer <token>"},
                         [(b"www-authenticate", b"Bearer")])
            return
        if identity.role == "listener":
            await _reply(send, 403, {"detail": "Listener accounts can only use the listening page"})
            return
        token = _authorization.set(conn.headers.get("authorization"))
        try:
            await self._app(scope, receive, send)
        finally:
            _authorization.reset(token)


async def _reply(send: Send, status: int, body: dict, headers: Optional[list] = None) -> None:
    payload = json.dumps(body).encode()
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(payload)).encode()), *(headers or [])],
    })
    await send({"type": "http.response.body", "body": payload})
