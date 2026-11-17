"""The core's control socket: HTTP/1.1 with JSON bodies over a Unix socket.

Stage 3 slice 7a (#229). This is the transport chosen in ADR 0005 ("Control
transport"). This first slice is **read only**: health and status, as the Stage
2 contract messages. Commands and configuration come in later slices.

Rules the server keeps, because it runs in the process that keeps the
repeater on the air:

- It uses the standard library only. No web framework.
- A handler reads in-memory state and answers. It never blocks, never touches a
  device and never takes a lock the audio path needs.
- A client can be slow, silent, malformed, oversized or gone mid-answer. None of
  that may affect the controller tick or the audio path: every connection has a
  deadline, requests are small, and every error is contained to its connection.
- The socket is created with a restrictive mode and removed on shutdown.
- If the server cannot start, the core carries on without it.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import stat
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable, Optional

from contracts import (
    CoreStatus,
    HealthCheck,
    HealthReport,
    HealthState,
    Header,
    PttEvidence,
    Sequencer,
    Source,
    StructuredError,
    new_id,
)

_logger = logging.getLogger("moreopenrepeater.core.control")

SOCKET_MODE = 0o660  # owner and group; the API's user shares the group
DIRECTORY_MODE = 0o750
MAX_REQUEST_LINE = 2048
MAX_HEADER_BYTES = 8192
MAX_HEADERS = 50
REQUEST_DEADLINE_SECONDS = 5.0  # to send a whole request
RESPONSE_DEADLINE_SECONDS = 5.0  # to take the answer
MAX_CONNECTIONS = 16
#: Longer than any normal pause (the tick runs every 50 ms); the same figure the
#: systemd watchdog uses.
STALL_SECONDS = 10.0

_REASONS = {200: "OK", 400: "Bad Request", 404: "Not Found", 405: "Method Not Allowed",
            408: "Request Timeout", 431: "Request Header Fields Too Large", 503: "Service Unavailable"}


class ControlServer:
    def __init__(
        self,
        path: Path,
        *,
        service,
        runtime,
        version: str = "0.1.0",
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """`service` is the `RepeaterService`; `runtime` the `CoreRuntime`."""
        self.path = path
        self._service = service
        self._runtime = runtime
        self._version = version
        self._clock = clock
        self._started = clock()
        self._sequencer = Sequencer()
        self._server: Optional[asyncio.AbstractServer] = None
        self._open = 0
        self.refused = 0  # connections turned away at the limit
        self._routes: dict[str, Callable[[], Awaitable[dict] | dict]] = {
            "/health": self._health,
            "/status": self._status,
        }

    # -- lifecycle ------------------------------------------------------------

    async def start(self) -> bool:
        """Start serving. Returns False (and logs) if it cannot; the core carries on."""
        try:
            directory = self.path.parent
            directory.mkdir(parents=True, exist_ok=True, mode=DIRECTORY_MODE)
            self._remove_stale()
            previous = os.umask(0o117)  # the socket is created 0660 whatever the umask
            try:
                self._server = await asyncio.start_unix_server(self._connection, path=str(self.path))
            finally:
                os.umask(previous)
            os.chmod(self.path, SOCKET_MODE)
        except OSError as error:
            _logger.error("control socket %s could not start: %s; the core runs without it", self.path, error)
            self._server = None
            return False
        _logger.info("control socket listening at %s", self.path)
        return True

    def _remove_stale(self) -> None:
        """A socket left by a crashed core. Anything else at the path is not ours to remove."""
        try:
            mode = os.lstat(self.path).st_mode
        except FileNotFoundError:
            return
        if not stat.S_ISSOCK(mode):
            raise OSError(f"{self.path} exists and is not a socket")
        os.unlink(self.path)

    async def stop(self) -> None:
        server, self._server = self._server, None
        if server is not None:
            server.close()
            try:
                await asyncio.wait_for(server.wait_closed(), 2.0)
            except asyncio.TimeoutError:
                pass
        try:
            if stat.S_ISSOCK(os.lstat(self.path).st_mode):
                os.unlink(self.path)
        except OSError:
            pass

    # -- one connection ---------------------------------------------------------

    async def _connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if self._open >= MAX_CONNECTIONS:
            self.refused += 1
            writer.close()
            return
        self._open += 1
        try:
            try:
                method, target = await asyncio.wait_for(self._read_request(reader), REQUEST_DEADLINE_SECONDS)
            except asyncio.TimeoutError:
                await self._respond(writer, 408, {"error": "request timed out"})
                return
            except _BadRequest as error:
                await self._respond(writer, error.status, {"error": error.message})
                return
            status, body = await self._dispatch(method, target)
            await self._respond(writer, status, body)
        except (ConnectionError, OSError):
            pass  # the client went away; nothing to do
        except Exception:  # noqa: BLE001 - one connection's bug must never reach the core
            _logger.exception("control socket: unexpected error")
        finally:
            self._open -= 1
            try:
                writer.close()
            except Exception:  # noqa: BLE001
                pass

    async def _read_request(self, reader: asyncio.StreamReader) -> tuple[str, str]:
        try:
            line = await reader.readuntil(b"\r\n")
        except asyncio.LimitOverrunError:
            raise _BadRequest(400, "request line too long") from None
        except asyncio.IncompleteReadError:
            raise _BadRequest(400, "incomplete request") from None
        if len(line) > MAX_REQUEST_LINE:
            raise _BadRequest(400, "request line too long")
        parts = line.decode("latin-1").rstrip("\r\n").split(" ")
        if len(parts) != 3 or not parts[2].startswith("HTTP/1."):
            raise _BadRequest(400, "malformed request line")
        method, target = parts[0], parts[1]
        size = headers = 0
        while True:
            try:
                header = await reader.readuntil(b"\r\n")
            except (asyncio.LimitOverrunError, asyncio.IncompleteReadError):
                raise _BadRequest(400, "malformed headers") from None
            if header == b"\r\n":
                break
            size += len(header)
            headers += 1
            if size > MAX_HEADER_BYTES or headers > MAX_HEADERS:
                raise _BadRequest(431, "headers too large")
            name = header.split(b":", 1)[0].strip().lower()
            if name in (b"content-length", b"transfer-encoding"):
                raise _BadRequest(400, "this endpoint takes no request body")
        return method, target

    async def _dispatch(self, method: str, target: str) -> tuple[int, dict]:
        path = target.split("?", 1)[0]
        handler = self._routes.get(path)
        if handler is None:
            return 404, {"error": f"no such path: {path}"}
        if method != "GET":
            return 405, {"error": "read only: use GET"}
        try:
            result = handler()
            if asyncio.iscoroutine(result):
                result = await result
            return 200, result
        except Exception:  # noqa: BLE001
            _logger.exception("control socket: %s failed", path)
            return 503, {"error": "could not read the core's state"}

    async def _respond(self, writer: asyncio.StreamWriter, status: int, body: dict) -> None:
        payload = json.dumps(body).encode()
        head = (
            f"HTTP/1.1 {status} {_REASONS.get(status, 'Error')}\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {len(payload)}\r\n"
            "Cache-Control: no-store\r\n"
            "Connection: close\r\n\r\n"
        ).encode()
        writer.write(head + payload)
        await asyncio.wait_for(writer.drain(), RESPONSE_DEADLINE_SECONDS)

    # -- contract messages --------------------------------------------------------

    def _header(self, request_id: Optional[str] = None) -> Header:
        return Header(
            schema_version=1,
            message_id=new_id(),
            emitted_at=datetime.now(timezone.utc),
            boot_id=self._sequencer.boot_id,
            seq=self._sequencer.next(),
            source=Source(module="core", version=self._version),
            request_id=request_id,
        )

    def _status(self) -> dict:
        snapshot = self._service.snapshot()
        message = CoreStatus(
            header=self._header(),
            state=snapshot.state,
            transmitter_enabled=snapshot.transmitter_enabled,
            locked_out=snapshot.locked_out,
            cos_active=snapshot.cos_active,
            # `ptt_active` is the controller's intent. Nothing here observes PTT or RF,
            # so nothing is claimed about it (inventory finding F6).
            ptt_commanded=snapshot.ptt_active,
            ptt_observed=None,
            ptt_evidence=PttEvidence.NONE,
            tot_remaining_s=snapshot.tot_remaining,
            hang_remaining_s=snapshot.hang_remaining,
            id_due_in_s=snapshot.id_due_in,
        )
        return json.loads(message.model_dump_json())

    def _health(self) -> dict:
        now = self._clock()
        checks = [self._progress_check("controller.tick", self._runtime.last_tick[0], now, required=True)]
        checks.append(self._progress_check("audio.engine", self._runtime.live_audio.progress(), now, required=False))
        checks.append(self._progress_check("link_radio.engine", self._runtime.link_radio.progress(), now, required=False))
        worst = max((c.state for c in checks), key=_RANK.__getitem__)
        message = HealthReport(
            header=self._header(),
            state=worst,
            uptime_s=max(0.0, now - self._started),
            checks=checks,
        )
        return json.loads(message.model_dump_json())

    @staticmethod
    def _progress_check(name: str, last: Optional[float], now: float, *, required: bool) -> HealthCheck:
        """Fresh progress is ok; stale is failed. No progress yet is ok for an
        optional part that is not running (audio off, no link radio)."""
        if last is None:
            if required:
                return HealthCheck(
                    name=name, state=HealthState.FAILED,
                    error=StructuredError(code="progress.none", message="has not run yet", retryable=True),
                )
            return HealthCheck(name=name, state=HealthState.OK)
        if now - last > STALL_SECONDS:
            return HealthCheck(
                name=name, state=HealthState.FAILED,
                error=StructuredError(code="progress.stalled", message=f"no progress for over {STALL_SECONDS:.0f} s"),
            )
        return HealthCheck(name=name, state=HealthState.OK)


_RANK = {HealthState.OK: 0, HealthState.DEGRADED: 1, HealthState.FAILED: 2, HealthState.STARTING: 0}


class _BadRequest(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def socket_path_from_env(env: dict[str, str]) -> Optional[Path]:
    """`MOREOPENREPEATER_CORE_SOCKET`; unset means no control socket."""
    value = (env.get("MOREOPENREPEATER_CORE_SOCKET") or "").strip()
    return Path(value) if value else None

