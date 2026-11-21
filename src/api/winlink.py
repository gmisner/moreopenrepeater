"""Is the optional Winlink RMS gateway up? Three quick network probes, nothing more (ADR 0006).

The gateway (`winlink`, LinBPQ) and its packet modem (`direwolf`, Dire Wolf) are separate containers behind the compose profile
`winlink`. The API has no Docker socket, so it cannot start, stop or inspect them. All it can do is knock on three ports on the
private services network and say which answered:

- the gateway's admin web page (any HTTP answer counts, a login prompt included),
- the gateway's telnet port (a TCP connection is accepted),
- the modem's KISS port (a TCP connection is accepted).

Rules this module keeps, because a dead or slow gateway must never slow the dashboard or the radio:

- Everything runs on the event loop with asyncio, never in a thread that the rest of the API waits for.
- Each probe has a hard timeout, and the three run together, so one status read costs at most one timeout.
- The result is cached for `CACHE_SECONDS`, and callers that arrive while a probe is running share it. A page that polls cannot
  hammer the gateway.
- A probe sends no credentials and reads no message text: it connects, and for the admin page reads only the HTTP status line.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from typing import Callable, Mapping, Optional

from observability import log_event

_logger = logging.getLogger("moreopenrepeater.winlink")

PROBE_TIMEOUT_SECONDS = 2.0
CACHE_SECONDS = 15.0

GATEWAY_ADMIN = "gateway_admin"
GATEWAY_TELNET = "gateway_telnet"
MODEM_KISS = "modem_kiss"
CHECK_IDS = (GATEWAY_ADMIN, GATEWAY_TELNET, MODEM_KISS)

DEFAULT_ADMIN_PORT = 8080
DEFAULT_TELNET_PORT = 8010
DEFAULT_KISS_PORT = 8001

_CALLSIGN = re.compile(r"^[A-Z0-9]{3,7}$")  # what the gateway's own entrypoint accepts

PROBLEMS = {
    GATEWAY_ADMIN: "The gateway's admin page is not answering. Look at `docker compose logs winlink`.",
    GATEWAY_TELNET: "The gateway's telnet port is not accepting connections. Look at `docker compose logs winlink`.",
    MODEM_KISS: (
        "The packet modem is not accepting connections, so the gateway cannot send or hear packets. "
        "Look at `docker compose logs direwolf`: a wrong sound card number is a common cause."
    ),
}


@dataclass(frozen=True)
class Target:
    host: str
    port: int


@dataclass(frozen=True)
class WinlinkSettings:
    """Where to knock. A missing host means this install has no such service, which is not an error."""

    gateway_host: Optional[str] = None
    modem_host: Optional[str] = None
    admin_port: int = DEFAULT_ADMIN_PORT
    telnet_port: int = DEFAULT_TELNET_PORT
    kiss_port: int = DEFAULT_KISS_PORT
    callsign: Optional[str] = None  # the station's callsign, without the gateway's SSID (the dashboard cannot read it)
    status_dir: Optional[str] = None  # where the gateway and modem containers write their sessions and counters (winlink_activity.py)

    def targets(self) -> dict[str, Optional[Target]]:
        gateway = self.gateway_host
        modem = self.modem_host
        return {
            GATEWAY_ADMIN: Target(gateway, self.admin_port) if gateway else None,
            GATEWAY_TELNET: Target(gateway, self.telnet_port) if gateway else None,
            MODEM_KISS: Target(modem, self.kiss_port) if modem else None,
        }


def _port(env: Mapping[str, str], name: str, default: int) -> int:
    try:
        value = int(env.get(name, "").strip())
    except ValueError:
        return default
    return value if 1 <= value <= 65535 else default


def settings_from_env(env: Mapping[str, str]) -> WinlinkSettings:
    call = env.get("MOREOPENREPEATER_WINLINK_CALL", "").strip().upper()
    callsign = call if _CALLSIGN.match(call) else None
    return WinlinkSettings(
        gateway_host=env.get("MOREOPENREPEATER_WINLINK_HOST", "").strip() or None,
        modem_host=env.get("MOREOPENREPEATER_WINLINK_MODEM_HOST", "").strip() or None,
        admin_port=_port(env, "MOREOPENREPEATER_WINLINK_ADMIN_PORT", DEFAULT_ADMIN_PORT),
        telnet_port=_port(env, "MOREOPENREPEATER_WINLINK_TELNET_PORT", DEFAULT_TELNET_PORT),
        kiss_port=_port(env, "MOREOPENREPEATER_WINLINK_KISS_PORT", DEFAULT_KISS_PORT),
        callsign=callsign,
        status_dir=env.get("MOREOPENREPEATER_WINLINK_STATUS_DIR", "").strip() or None,
    )


async def _close(writer: asyncio.StreamWriter) -> None:
    writer.close()
    try:
        await asyncio.wait_for(writer.wait_closed(), 0.5)
    except (OSError, asyncio.TimeoutError):
        pass


async def _connect(target: Target) -> tuple[bool, str]:
    try:
        _, writer = await asyncio.open_connection(target.host, target.port)
    except ConnectionRefusedError:
        return False, "Connection refused: nothing is listening there."
    except OSError as error:
        if isinstance(error, TimeoutError):
            return False, "No answer."
        return False, "Could not reach it (the service may not be running)."
    await _close(writer)
    return True, "Accepting connections."


async def _http_answers(target: Target) -> tuple[bool, str]:
    try:
        reader, writer = await asyncio.open_connection(target.host, target.port)
    except ConnectionRefusedError:
        return False, "Connection refused: nothing is listening there."
    except OSError:
        return False, "Could not reach it (the service may not be running)."
    try:
        request = f"GET / HTTP/1.0\r\nHost: {target.host}\r\nConnection: close\r\n\r\n"
        writer.write(request.encode("ascii", "replace"))
        await writer.drain()
        line = await reader.readline()
    except OSError:
        return False, "Connected, but the connection dropped."
    finally:
        await _close(writer)
    if line.startswith(b"HTTP/"):  # any status counts: 200, or a login prompt (401), or a redirect
        return True, "Answering."
    return False, "Connected, but it did not answer like a web page."


async def _probe(kind: str, target: Optional[Target], timeout: float) -> dict:
    if target is None:
        return {"id": kind, "ok": False, "reason": "Not set up on this install."}
    try:
        work = _http_answers(target) if kind == GATEWAY_ADMIN else _connect(target)
        ok, reason = await asyncio.wait_for(work, timeout)
    except asyncio.TimeoutError:
        ok, reason = False, "No answer."
    except Exception:  # noqa: BLE001 - a probe must never raise into the dashboard
        ok, reason = False, "The check itself failed."
    return {"id": kind, "ok": ok, "reason": reason}


class WinlinkStatus:
    """The cached, never-blocking answer to "is the Winlink gateway running?"."""

    def __init__(
        self,
        settings: WinlinkSettings,
        *,
        timeout: float = PROBE_TIMEOUT_SECONDS,
        cache_seconds: float = CACHE_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.settings = settings
        self.timeout = timeout
        self.cache_seconds = cache_seconds
        self._clock = clock
        self._cached: Optional[tuple[float, dict]] = None
        self._running: Optional[asyncio.Future] = None
        self._last_signature: Optional[tuple] = None

    async def status(self) -> dict:
        cached = self._cached
        if cached is not None and self._clock() - cached[0] < self.cache_seconds:
            return cached[1]
        if self._running is None or self._running.done():
            self._running = asyncio.ensure_future(self._refresh())
        # Shielded: a client that hangs up must not cancel the probe other callers are waiting for.
        return await asyncio.shield(self._running)

    async def _refresh(self) -> dict:
        targets = self.settings.targets()
        checks = await asyncio.gather(*(_probe(kind, targets[kind], self.timeout) for kind in CHECK_IDS))
        result = self._describe(list(checks))
        self._cached = (self._clock(), result)
        self._note(result)
        return result

    def _describe(self, checks: list[dict]) -> dict:
        enabled = any(check["ok"] for check in checks)
        problems = [PROBLEMS[check["id"]] for check in checks if not check["ok"]] if enabled else []
        return {"enabled": enabled, "callsign": self.settings.callsign, "checks": checks, "problems": problems}

    def _note(self, result: dict) -> None:
        """Say so when the picture changes, not on every probe. A gateway that was never turned on stays silent."""
        ok = {check["id"]: check["ok"] for check in result["checks"]}
        signature = (result["enabled"], *(ok[kind] for kind in CHECK_IDS))
        previous, self._last_signature = self._last_signature, signature
        if signature == previous or (previous is None and not result["enabled"]):
            return
        failing = [kind for kind in CHECK_IDS if not ok[kind]]
        severity = logging.WARNING if result["enabled"] and failing else logging.INFO
        log_event(
            _logger, severity, "winlink.status_changed",
            "Winlink gateway status changed" if result["enabled"] else "Winlink gateway is not running",
            enabled=result["enabled"], failing=",".join(failing), facts_in_text=False,
        )
