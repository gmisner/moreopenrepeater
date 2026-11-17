"""The core's control socket: HTTP/1.1 with JSON bodies over a Unix socket.

Stage 3 slice 7 (#229). This is the transport chosen in ADR 0005 ("Control
transport"). Health and status are read only, as the Stage 2 contract messages.
`GET /events` (slice 7c-2) streams status changes and a heartbeat as server-sent
events. `POST /speak` (slice 7b) is the first command: a `speak.request` in, a
`speak.decision` out. Configuration changes come in later slices.

Rules the server keeps, because it runs in the process that keeps the
repeater on the air:

- It uses the standard library only. No web framework.
- A handler reads in-memory state and answers. It never blocks the event loop,
  never touches a device and never takes a lock the audio path needs. Slow work
  (speech synthesis) runs in the executor under a deadline.
- A client can be slow, silent, malformed, oversized or gone mid-answer. None of
  that may affect the controller tick or the audio path: every connection has a
  deadline, requests are small (a body only on POST, 16 KiB at most), and every
  error is contained to its connection.
- Content a client sends (the text to speak) is never logged and never echoed
  back in an error.
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

from pydantic import ValidationError

from contracts import (
    CoreStatus,
    HealthCheck,
    HealthReport,
    HealthState,
    Header,
    IdempotencyLedger,
    Priority,
    PttEvidence,
    Sequencer,
    SpeakDecision,
    SpeakRequest,
    SpeakStatus,
    Source,
    StructuredError,
    new_id,
    screen_speak_request,
)
from playout.renderer import TTS_PREFIX, UnknownClipError
from playout.tts import TTSError

_logger = logging.getLogger("moreopenrepeater.core.control")

SOCKET_MODE = 0o660  # owner and group; the API's user shares the group
DIRECTORY_MODE = 0o750
MAX_REQUEST_LINE = 2048
MAX_HEADER_BYTES = 8192
MAX_HEADERS = 50
REQUEST_DEADLINE_SECONDS = 5.0  # to send a whole request
RESPONSE_DEADLINE_SECONDS = 5.0  # to take the answer
MAX_CONNECTIONS = 16
MAX_STREAMS = 4  # long-lived /events connections; fewer than MAX_CONNECTIONS so they cannot starve the rest
HEARTBEAT_SECONDS = 5.0
STREAM_WRITE_DEADLINE_SECONDS = 5.0  # a client that cannot take an event in this long is dropped
MIN_EVENT_INTERVAL_SECONDS = 0.1  # status events are coalesced to at most ten a second
MAX_BODY_BYTES = 16 * 1024
RENDER_DEADLINE_SECONDS = 30.0  # to prepare speech before giving up on a request
#: Longer than any normal pause (the tick runs every 50 ms); the same figure the
#: systemd watchdog uses.
STALL_SECONDS = 10.0

_REASONS = {200: "OK", 400: "Bad Request", 404: "Not Found", 405: "Method Not Allowed",
            408: "Request Timeout", 411: "Length Required", 413: "Payload Too Large",
            415: "Unsupported Media Type", 431: "Request Header Fields Too Large", 503: "Service Unavailable"}


class ControlServer:
    def __init__(
        self,
        path: Path,
        *,
        service,
        runtime,
        version: str = "0.1.0",
        clock: Callable[[], float] = time.monotonic,
        now: Optional[Callable[[], datetime]] = None,
    ) -> None:
        """`service` is the `RepeaterService`; `runtime` the `CoreRuntime`. `clock` is
        monotonic time (health); `now` is wall-clock time (request deadlines)."""
        self.path = path
        self._service = service
        self._runtime = runtime
        self._version = version
        self._clock = clock
        self._started = clock()
        self._sequencer = Sequencer()
        self._server: Optional[asyncio.AbstractServer] = None
        self._open = 0
        self._streams = 0
        self._writers: set[asyncio.StreamWriter] = set()
        self.refused = 0  # connections turned away at the limit
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._ledger = IdempotencyLedger()
        self._get: dict[str, Callable[[], dict]] = {"/health": self._health, "/status": self._status}
        self._post: dict[str, Callable[[bytes], Awaitable[dict]]] = {"/speak": self._speak}

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
            for writer in list(self._writers):  # end open connections too, /events streams included
                writer.close()
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
        self._writers.add(writer)
        try:
            try:
                method, target, body = await asyncio.wait_for(self._read_request(reader), REQUEST_DEADLINE_SECONDS)
            except asyncio.TimeoutError:
                await self._respond(writer, 408, {"error": "request timed out"})
                return
            except _BadRequest as error:
                await self._respond(writer, error.status, error.body())
                return
            if method == "GET" and target.split("?", 1)[0] == "/events":
                await self._stream_events(reader, writer)
                return
            status, answer = await self._dispatch(method, target, body)
            await self._respond(writer, status, answer)
        except (ConnectionError, OSError):
            pass  # the client went away; nothing to do
        except Exception:  # noqa: BLE001 - one connection's bug must never reach the core
            _logger.exception("control socket: unexpected error")
        finally:
            self._open -= 1
            self._writers.discard(writer)
            try:
                writer.close()
            except Exception:  # noqa: BLE001
                pass

    async def _read_request(self, reader: asyncio.StreamReader) -> tuple[str, str, bytes]:
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
        size = count = 0
        length: Optional[str] = None
        content_type = ""
        while True:
            try:
                header = await reader.readuntil(b"\r\n")
            except (asyncio.LimitOverrunError, asyncio.IncompleteReadError):
                raise _BadRequest(400, "malformed headers") from None
            if header == b"\r\n":
                break
            size += len(header)
            count += 1
            if size > MAX_HEADER_BYTES or count > MAX_HEADERS:
                raise _BadRequest(431, "headers too large")
            name, _, value = header.partition(b":")
            name = name.strip().lower()
            if name == b"transfer-encoding":
                raise _BadRequest(400, "chunked requests are not supported")
            if name == b"content-length":
                if length is not None:
                    raise _BadRequest(400, "more than one content-length")
                length = value.strip().decode("latin-1")
            elif name == b"content-type":
                content_type = value.strip().decode("latin-1").split(";", 1)[0].strip().lower()
        if method != "POST":
            if length not in (None, "0"):
                raise _BadRequest(400, "this endpoint takes no request body")
            return method, target, b""
        if length is None:
            raise _BadRequest(411, "a content-length is required")
        if not length.isdigit():
            raise _BadRequest(400, "bad content-length")
        if int(length) > MAX_BODY_BYTES:
            raise _BadRequest(413, f"the body may be at most {MAX_BODY_BYTES} bytes")
        if content_type != "application/json":
            raise _BadRequest(415, "send application/json")
        try:
            body = await reader.readexactly(int(length))
        except asyncio.IncompleteReadError:
            raise _BadRequest(400, "incomplete body") from None
        return method, target, body

    async def _dispatch(self, method: str, target: str, body: bytes) -> tuple[int, dict]:
        path = target.split("?", 1)[0]
        if path not in self._get and path not in self._post:
            return 404, {"error": f"no such path: {path}"}
        try:
            if method == "GET" and path in self._get:
                return 200, self._get[path]()
            if method == "POST" and path in self._post:
                return 200, await self._post[path](body)
            allowed = "GET" if path in self._get else "POST"
            return 405, {"error": f"use {allowed} for {path}"}
        except _BadRequest as error:
            return error.status, error.body()
        except Exception:  # noqa: BLE001
            _logger.exception("control socket: %s failed", path)
            return 503, {"error": "could not complete the request"}

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

    def _header(self, request_id: Optional[str] = None, correlation_id: Optional[str] = None) -> Header:
        return Header(
            schema_version=1,
            message_id=new_id(),
            emitted_at=datetime.now(timezone.utc),
            boot_id=self._sequencer.boot_id,
            seq=self._sequencer.next(),
            source=Source(module="core", version=self._version),
            request_id=request_id,
            correlation_id=correlation_id,
        )

    def _status(self) -> dict:
        return self._status_from(self._service.snapshot())

    def _status_from(self, snapshot) -> dict:
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
            ctcss_hz=snapshot.ctcss_hz,
            linked_nodes=sorted(snapshot.linked_nodes),
            net_active=snapshot.net_active,
            gmrs_mode=snapshot.gmrs_mode,
            callsign=snapshot.callsign,
        )
        return json.loads(message.model_dump_json())

    # -- events (server-sent) ------------------------------------------------------------

    async def _stream_events(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """`GET /events`: the current status at once, then a `status` event whenever it
        changes (at most ten a second) and a `heartbeat` every few seconds. A client
        that falls behind or goes away is dropped; it never slows the core."""
        if self._streams >= MAX_STREAMS:
            await self._respond(writer, 503, {"error": "too many event streams"})
            return
        self._streams += 1
        queue = self._service.subscribe()  # holds only the latest snapshot
        gone = asyncio.ensure_future(_until_eof(reader))  # finishes when the client hangs up
        waiting: Optional[asyncio.Future] = None
        try:
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nCache-Control: no-store\r\n"
                b"Connection: close\r\nX-Accel-Buffering: no\r\n\r\n"
            )
            await self._send_event(writer, "status", self._event_payload(self._service.snapshot()))
            while True:
                waiting = asyncio.ensure_future(queue.get())
                done, _ = await asyncio.wait({waiting, gone}, timeout=HEARTBEAT_SECONDS, return_when=asyncio.FIRST_COMPLETED)
                if gone in done:
                    return
                if waiting in done:
                    await self._send_event(writer, "status", self._event_payload(waiting.result()))
                    await asyncio.sleep(MIN_EVENT_INTERVAL_SECONDS)  # coalesce a burst into the latest
                else:
                    waiting.cancel()
                    await self._send_event(writer, "heartbeat", self._heartbeat_payload())
        finally:
            for task in (gone, waiting):
                if task is not None and not task.done():
                    task.cancel()
            self._service.unsubscribe(queue)
            self._streams -= 1

    def _event_payload(self, snapshot) -> dict:
        # `local` is for the API only (the trusted channel): the last clip name can carry
        # the text of an announcement, so it is not part of the module-facing contract.
        return {"status": self._status_from(snapshot), "local": {"last_clip": snapshot.last_clip, "timestamp": snapshot.timestamp}}

    def _heartbeat_payload(self) -> dict:
        health = self._health()
        return {"at": datetime.now(timezone.utc).isoformat(), "seq": self._sequencer.next(), "health": health["state"]}

    async def _send_event(self, writer: asyncio.StreamWriter, name: str, data: dict) -> None:
        writer.write(f"event: {name}\ndata: {json.dumps(data)}\n\n".encode())
        await asyncio.wait_for(writer.drain(), STREAM_WRITE_DEADLINE_SECONDS)

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

    # -- speak.request ----------------------------------------------------------------

    def _decision(
        self,
        request: SpeakRequest,
        status: SpeakStatus,
        *,
        transmission_id: Optional[str] = None,
        error: Optional[StructuredError] = None,
    ) -> dict:
        message = SpeakDecision(
            header=self._header(request_id=request.header.message_id, correlation_id=request.header.correlation_id),
            status=status,
            transmission_id=transmission_id,
            error=error,
        )
        return json.loads(message.model_dump_json())

    async def _speak(self, body: bytes) -> dict:
        """A module asks the core to say something. The core decides.

        Always answers 200 with a `speak.decision` for a well-formed request:
        accepted, duplicate, expired or rejected. The text is content and is never
        logged or echoed back.
        """
        try:
            request = SpeakRequest.model_validate_json(body)
        except ValidationError as error:
            # Locations and kinds only: a validation message can quote the input.
            problems = [{"at": [str(part) for part in e["loc"]], "type": e["type"]} for e in error.errors()]
            raise _BadRequest(400, "not a valid speak.request", problems=problems) from None

        status, error_message = screen_speak_request(request, self._now(), self._ledger)
        if status is not SpeakStatus.ACCEPTED:
            return self._decision(request, status, error=error_message)
        try:
            outcome = await self._queue_speech(request)
        except BaseException:
            self._ledger.forget(request.idempotency_key)  # never queued: a retry is not a duplicate
            raise
        if outcome is not None:
            self._ledger.forget(request.idempotency_key)
            decision_status, error = outcome
            return self._decision(request, decision_status, error=error)
        _logger.info("speak.request accepted (topic %s)", request.topic)
        return self._decision(request, SpeakStatus.ACCEPTED, transmission_id=new_id())

    async def _queue_speech(self, request: SpeakRequest) -> Optional[tuple[SpeakStatus, StructuredError]]:
        """Queue the speech. Returns None when queued, else why not."""
        service = self._service

        def rejected(code: str, message: str, retryable: bool) -> tuple[SpeakStatus, StructuredError]:
            return SpeakStatus.REJECTED, StructuredError(code=code, message=message, retryable=retryable)

        if not service.config.transmitter_enabled:
            return rejected("speak.transmitter_disabled", "The transmitter is disabled.", False)
        clip = TTS_PREFIX + request.text
        emergency = request.priority >= Priority.EMERGENCY
        if service.net_active and service.saved_config.net_hold_announcements and not emergency:
            service.defer_announcement(clip)  # held until the net ends, like a scheduled weather report
            return None
        loop = asyncio.get_running_loop()
        try:
            # In the executor, so synthesis never blocks the tick. A timed-out job keeps
            # running in its thread (it cannot be cancelled) but its result is dropped.
            await asyncio.wait_for(
                loop.run_in_executor(None, self._runtime.renderer.render, clip, service.config), RENDER_DEADLINE_SECONDS
            )
        except asyncio.TimeoutError:
            return rejected("speak.render_timeout", "Preparing the speech took too long.", True)
        except (TTSError, UnknownClipError):
            return rejected("speak.render_failed", "The speech could not be prepared.", True)
        if request.is_expired(self._now()):
            return SpeakStatus.EXPIRED, StructuredError(
                code="speak.expired", message="The deadline passed while the speech was being prepared."
            )
        if not service.queue_announcement(clip, priority=int(request.priority)):
            return rejected("speak.queue_full", "Too many announcements are already waiting to play.", True)
        return None

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


async def _until_eof(reader: asyncio.StreamReader) -> None:
    """Returns when the client closes its end (or the connection breaks)."""
    try:
        await reader.read()
    except (ConnectionError, OSError):
        pass


class _BadRequest(Exception):
    def __init__(self, status: int, message: str, problems: Optional[list] = None) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.problems = problems

    def body(self) -> dict:
        body: dict = {"error": self.message}
        if self.problems is not None:
            body["problems"] = self.problems
        return body


def socket_path_from_env(env: dict[str, str]) -> Optional[Path]:
    """`MOREOPENREPEATER_CORE_SOCKET`; unset means no control socket."""
    value = (env.get("MOREOPENREPEATER_CORE_SOCKET") or "").strip()
    return Path(value) if value else None

