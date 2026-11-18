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
import dataclasses
import inspect
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
    HARDWARE,
    PROGRAMMING,
    SIMULATED,
    ActivityEntry,
    ActivityRows,
    CommandName,
    CommandResult,
    CommandStatus,
    ConfigApplied,
    ConfigChange,
    ConfigResult,
    ConfigState,
    CoreCommand,
    CoreHardware,
    CoreLogs,
    LinkEvents,
    ProgrammingState,
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
import dataclasses as _dc

from api.models import SnapshotImportRequest
from api.models.programming import AnnouncementFields, MacroCreateRequest
from api.service.snapshot import config_from_snapshot
from api.snapshot_import import snapshot_to_import
from controller.announcements import Announcement
from controller.macros import Macro
from api.core_runtime import AnnouncementQueueFull
from api.gpio import GpioError
from playout.renderer import TTS_PREFIX, UnknownClipError

from .config_boundary import ConfigRejected, CoreConfigError, check_change, check_config
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
#: A countdown (time-out, hang time, next ID) moves every tick, so it is not a change
#: by itself. The client counts it down on its own; a new event is sent only when the
#: real value is off from that count by more than this, or starts or stops existing.
COUNTDOWN_TOLERANCE_SECONDS = 1.0
MAX_BODY_BYTES = 16 * 1024
#: A configuration change can carry a few large settings; still bounded.
MAX_CONFIG_BODY_BYTES = 64 * 1024
#: A command can carry a whole snapshot (a restore): every setting, macro and announcement.
MAX_COMMAND_BODY_BYTES = 256 * 1024
HARDWARE_DEADLINE_SECONDS = 2.0  # each part's status must answer in this long
HARDWARE_PARTS = (
    "live_audio", "gpio", "tx_fan", "link_radio", "remote_base", "monitor_receiver", "autopatch", "allstar_node", "dmr",
)
LOGS_PER_REQUEST = 500  # the most log lines one GET /logs returns
MAX_ACTIVITY_ROWS = 20_000  # the most rows one GET /activity returns; a reader asks again for the rest
LOGS_RING_MAX = 5000  # more than any hub holds
MAX_LOG_LINE_CHARS = 4000
#: The macro actions the API's modules own. A client names the ones it handles when it connects.
MACRO_FAMILIES = frozenset({"net", "homeassistant", "metar", "sensor", "mailbox", "aprs", "audit"})
MACRO_QUEUE_LIMIT = 32  # per client; a client that cannot keep up loses the newest
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
        allow_simulate: bool = False,
        assets_have: Optional[Callable[[str], bool]] = None,
        log_hub=None,
        activity_store=None,
    ) -> None:
        """`service` is the `RepeaterService`; `runtime` the `CoreRuntime`. `clock` is
        monotonic time (health); `now` is wall-clock time (request deadlines)."""
        self.path = path
        self._service = service
        self._runtime = runtime
        #: The simulator injects events the radio did not send, and a simulated COS makes the
        #: repeater transmit. A core on a real radio refuses it unless this was asked for.
        self._allow_simulate = allow_simulate
        self._log_hub = log_hub  # the core's recent log lines, for GET /logs
        self._activity_store = activity_store  # what the core recorded about use, for GET /activity
        self._assets_have = assets_have  # is there an uploaded clip with this id? None: not checked here
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
        self._get: dict[str, Callable[[], dict]] = {
            "/health": self._health, "/status": self._status, "/config": self._config_state,
            "/programming": self._programming_state, "/link-events": self._link_events,
            "/hardware": self._hardware,
        }
        self._post: dict[str, Callable[[bytes], Awaitable[dict]]] = {
            "/speak": self._speak, "/config": self._config_change, "/command": self._command,
        }
        self._body_limits = {"/config": MAX_CONFIG_BODY_BYTES, "/command": MAX_COMMAND_BODY_BYTES}
        #: Rises with every change to the configuration, whoever made it (the API, a macro,
        #: net mode). The API sends the version it last saw to detect a concurrent change.
        self._config_version = 1
        self._config_waiters: set[asyncio.Event] = set()  # one per open /events stream
        #: One per open /events stream: the macro families it handles and a queue for its events.
        self._macro_clients: list[tuple[frozenset, asyncio.Queue]] = []
        service.programming.api_action = self._api_action
        if service.programming.audit_hook is None:
            service.programming.audit_hook = self._audit
        service.add_config_listener(self._config_changed)

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
        if self._service.programming.api_action == self._api_action:
            self._service.programming.api_action = None  # macros fall back to "not available"
        if self._service.programming.audit_hook == self._audit:
            self._service.programming.audit_hook = None
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
                await self._stream_events(reader, writer, _handled_families(target))
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
        limit = self._body_limits.get(target.split("?", 1)[0], MAX_BODY_BYTES)
        if int(length) > limit:
            raise _BadRequest(413, f"the body may be at most {limit} bytes")
        if content_type != "application/json":
            raise _BadRequest(415, "send application/json")
        try:
            body = await reader.readexactly(int(length))
        except asyncio.IncompleteReadError:
            raise _BadRequest(400, "incomplete body") from None
        return method, target, body

    async def _dispatch(self, method: str, target: str, body: bytes) -> tuple[int, dict]:
        path = target.split("?", 1)[0]
        if path not in self._get and path not in self._post and path not in _QUERY_PATHS:
            return 404, {"error": f"no such path: {path}"}
        try:
            if method == "GET" and path == "/logs":
                return 200, self._logs(target)
            if method == "GET" and path == "/activity":
                return 200, await self._activity(target)
            if method == "GET" and path in self._get:
                result = self._get[path]()
                if asyncio.iscoroutine(result):
                    result = await result
                return 200, result
            if method == "POST" and path in self._post:
                return 200, await self._post[path](body)
            allowed = "GET" if (path in self._get or path in _QUERY_PATHS) else "POST"
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

    async def _stream_events(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, handles: frozenset = frozenset()
    ) -> None:
        """`GET /events`: the current status at once, then a `status` event whenever it
        changes (at most ten a second) and a `heartbeat` every few seconds. A client
        that falls behind or goes away is dropped; it never slows the core."""
        if self._streams >= MAX_STREAMS:
            await self._respond(writer, 503, {"error": "too many event streams"})
            return
        self._streams += 1
        queue = self._service.subscribe()  # holds only the latest snapshot
        gone = asyncio.ensure_future(_until_eof(reader))  # finishes when the client hangs up
        config_event = asyncio.Event()  # set when the configuration changes
        self._config_waiters.add(config_event)
        macro_queue: asyncio.Queue = asyncio.Queue(maxsize=MACRO_QUEUE_LIMIT)
        client = (handles, macro_queue)
        self._macro_clients.append(client)
        waiting: Optional[asyncio.Future] = None  # the next snapshot
        config_changed: Optional[asyncio.Future] = None
        macro_waiting: Optional[asyncio.Future] = None  # the next macro action for this client
        try:
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nCache-Control: no-store\r\n"
                b"Connection: close\r\nX-Accel-Buffering: no\r\n\r\n"
            )
            sent = self._service.snapshot()
            await self._send_event(writer, "status", self._event_payload(sent))
            last_write = time.monotonic()
            while True:
                if waiting is None:
                    waiting = asyncio.ensure_future(queue.get())
                if config_changed is None:
                    config_changed = asyncio.ensure_future(config_event.wait())
                if macro_waiting is None:
                    macro_waiting = asyncio.ensure_future(macro_queue.get())
                # A heartbeat is due HEARTBEAT_SECONDS after the last thing written, even
                # while snapshots keep arriving that are not worth sending.
                due = max(0.0, HEARTBEAT_SECONDS - (time.monotonic() - last_write))
                done, _ = await asyncio.wait(
                    {waiting, gone, config_changed, macro_waiting}, timeout=due, return_when=asyncio.FIRST_COMPLETED
                )
                if gone in done:
                    return
                if config_changed in done:
                    # The status may not have changed, but the version in it has: the client
                    # reads the configuration again when it sees a new version.
                    config_event.clear()
                    config_changed = None
                    sent = self._service.snapshot()
                    await self._send_event(writer, "status", self._event_payload(sent))
                    last_write = time.monotonic()
                if macro_waiting in done:
                    item = macro_waiting.result()
                    macro_waiting = None
                    await self._send_event(writer, "macro", item)
                    last_write = time.monotonic()
                if waiting in done:
                    snapshot = waiting.result()
                    waiting = None
                    now = time.monotonic()
                    if _is_news(sent, snapshot):
                        await self._send_event(writer, "status", self._event_payload(snapshot))
                        sent = snapshot
                        last_write = time.monotonic()
                        await asyncio.sleep(MIN_EVENT_INTERVAL_SECONDS)  # coalesce a burst into the latest
                    elif now - last_write >= HEARTBEAT_SECONDS:
                        await self._send_event(writer, "heartbeat", self._heartbeat_payload())
                        last_write = time.monotonic()
                if not done:
                    await self._send_event(writer, "heartbeat", self._heartbeat_payload())
                    last_write = time.monotonic()
        finally:
            for task in (gone, waiting, config_changed, macro_waiting):
                if task is not None and not task.done():
                    task.cancel()
            self._config_waiters.discard(config_event)
            if client in self._macro_clients:
                self._macro_clients.remove(client)
            self._service.unsubscribe(queue)
            self._streams -= 1

    def _event_payload(self, snapshot) -> dict:
        # `local` is for the API only (the trusted channel): the last clip name can carry
        # the text of an announcement, so it is not part of the module-facing contract.
        return {"status": self._status_from(snapshot), "local": {
            "last_clip": snapshot.last_clip, "timestamp": snapshot.timestamp, "config_version": self._config_version,
        }}

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

    # -- configuration (the trusted API channel) ------------------------------------------

    def _config_changed(self, _config) -> None:
        self._config_version += 1
        for waiter in self._config_waiters:
            waiter.set()  # wake each /events stream: a client may need to read the configuration again

    def _config_state(self) -> dict:
        """`GET /config`: the whole saved configuration, secrets included. This channel is the
        socket's file permissions; the body is never logged."""
        message = ConfigState(
            header=self._header(), config_version=self._config_version,
            values=self._service.saved_config.to_flat_dict(),
        )
        return json.loads(message.model_dump_json())

    def _config_result(self, request: ConfigChange, result: ConfigResult, error: Optional[StructuredError] = None) -> dict:
        message = ConfigApplied(
            header=self._header(request_id=request.header.message_id, correlation_id=request.header.correlation_id),
            config_version=self._config_version, result=result, error=error,
        )
        return json.loads(message.model_dump_json())

    async def _config_change(self, body: bytes) -> dict:
        """`POST /config`: a `config.change` in, a `config.applied` out. The core checks the
        change itself; a refused change applies nothing. The values are never logged or echoed."""
        try:
            request = ConfigChange.model_validate_json(body)
        except ValidationError as error:
            problems = [{"at": [str(part) for part in e["loc"]], "type": e["type"]} for e in error.errors()]
            raise _BadRequest(400, "not a valid config.change", problems=problems) from None
        if request.expected_version is not None and request.expected_version != self._config_version:
            return self._config_result(request, ConfigResult.REJECTED, StructuredError(
                code="config.conflict", retryable=True,
                message=f"the configuration changed since version {request.expected_version}; read it again",
            ))
        try:
            check_change(self._service.saved_config, request.values)
        except ConfigRejected as rejected:
            _logger.warning("config.change refused (%s)", rejected.code)
            return self._config_result(request, ConfigResult.REJECTED, StructuredError(
                code=rejected.code, message=rejected.message, retryable=False,
            ))
        try:
            self._service.update_config(**request.values)
        except Exception:  # noqa: BLE001 - nothing the client sent may reach the controller as a crash
            _logger.exception("config.change could not be applied")
            return self._config_result(request, ConfigResult.REJECTED, StructuredError(
                code="config.apply_failed", message="the core could not apply the change", retryable=True,
            ))
        _logger.info("config.change applied (%d settings: %s)", len(request.values), ", ".join(sorted(request.values)))
        return self._config_result(request, ConfigResult.APPLIED)

    def _api_action(self, family: str, args: list) -> bool:
        """A macro asked for something the API's modules do. Deliver it to every connected
        client that handles `family` and say whether any took it. It never waits, and the
        arguments (a mailbox PIN, say) are never logged."""
        taken = False
        for handles, queue in self._macro_clients:
            if family in handles:
                try:
                    queue.put_nowait({"family": family, "args": args})
                    taken = True
                except asyncio.QueueFull:
                    _logger.warning("a client is not keeping up; dropped a %s macro action for it", family)
        return taken

    def _audit(self, actor: str, action: str, detail: str) -> None:
        """Something a macro did that the dashboard's audit trail records. The core logs who and
        what (the detail can be a command or a message, so it is left out of the log) and tells
        every connected client that handles `audit`. While none is connected the trail has a gap;
        the core's log does not."""
        _logger.info("audit: %s: %s", actor, action)
        self._api_action("audit", [actor, action, detail])

    def _logs(self, target: str) -> dict:
        """`GET /logs?after=N&limit=M`: the core's recent log lines after cursor N, oldest first.
        The cursor is the hub's own sequence, which starts again when the core does; the answer's
        `header.boot_id` says which run it is."""
        query = dict(part.partition("=")[::2] for part in target.partition("?")[2].split("&") if part)
        try:
            after = max(0, int(query.get("after", "0")))
            limit = max(1, min(int(query.get("limit", str(LOGS_PER_REQUEST))), LOGS_PER_REQUEST))
        except ValueError:
            raise _BadRequest(400, "after and limit must be whole numbers") from None
        if self._log_hub is None:
            lines, cursor = [], 0
        else:
            # The oldest `limit` lines after the cursor, so a reader far behind catches up a page at a
            # time (the hub's own `since` returns the newest, which would skip the middle).
            everything, cursor = self._log_hub.since(after, LOGS_RING_MAX)
            found = everything[:limit]
            lines = [
                {"seq": e.seq, "line": e.line[:MAX_LOG_LINE_CHARS], "level": e.level, "at": e.at} for e in found
            ]
        message = CoreLogs(header=self._header(), cursor=cursor, lines=lines)
        return json.loads(message.model_dump_json())

    async def _activity(self, target: str) -> dict:
        """`GET /activity?since=&until=&kind=&limit=&newest=1`: what the core recorded about use of
        the repeater. By default the rows from `since` (unix time) up to `until`, oldest first, at
        most MAX_ACTIVITY_ROWS (`truncated` says there were more). With `newest=1` it is the most
        recent `limit` rows of one `kind`, newest first. The database is read off the event loop, so
        a long history cannot hold up the controller."""
        query = dict(part.partition("=")[::2] for part in target.partition("?")[2].split("&") if part)
        try:
            since = float(query.get("since", "0"))
            until = float(query["until"]) if "until" in query else time.time() + 1
            limit = max(1, min(int(query.get("limit", str(MAX_ACTIVITY_ROWS))), MAX_ACTIVITY_ROWS))
        except ValueError:
            raise _BadRequest(400, "since and until must be numbers and limit a whole number") from None
        kind = query.get("kind") or None
        newest = query.get("newest") == "1"
        if newest and kind is None:
            raise _BadRequest(400, "newest needs a kind")
        store = self._activity_store
        truncated = False
        if store is None:
            found: list = []
        elif newest:
            found = await asyncio.get_running_loop().run_in_executor(None, store.recent, kind, limit)
        else:
            found = await asyncio.get_running_loop().run_in_executor(None, store.rows, since, until, kind)
            if len(found) > limit:
                found, truncated = found[:limit], True
        message = ActivityRows(
            header=self._header(),
            rows=[
                ActivityEntry(kind=r.kind, started_at=r.started_at, duration=max(0.0, r.duration), timed_out=r.timed_out)
                for r in found
            ],
            truncated=truncated,
        )
        return json.loads(message.model_dump_json())

    async def _hardware(self) -> dict:
        """`GET /hardware`: what each part of the radio side reports, read at once and each with a
        deadline, so one slow part (the AllStar node reads files and asks Asterisk) cannot hold the
        answer or the controller. A part that fails or is slow is named in `unavailable`."""
        async def one(name: str):
            part = getattr(self._runtime, name, None)
            status = getattr(part, "status", None)
            if status is None:
                return name, None
            try:
                if inspect.iscoroutinefunction(status):
                    value = await asyncio.wait_for(status(), HARDWARE_DEADLINE_SECONDS)
                else:
                    value = status()
                return name, json.loads(json.dumps(value, default=str))  # plain JSON, whatever the part returns
            except Exception:  # noqa: BLE001 - one part must not take the others down
                return name, None

        statuses, missing = {}, []
        for name, value in await asyncio.gather(*(one(n) for n in HARDWARE_PARTS)):
            if isinstance(value, dict):
                statuses[name] = value
            else:
                missing.append(name)
        message = CoreHardware(header=self._header(), statuses=statuses, unavailable=missing)
        return json.loads(message.model_dump_json())

    def _link_events(self) -> dict:
        """`GET /link-events`: recent link activity, newest first (all the core keeps)."""
        message = LinkEvents(header=self._header(), events=self._service.links.recent_events(500))
        return json.loads(message.model_dump_json())

    def _programming_state(self) -> dict:
        """`GET /programming`: the macros and announcements, each announcement with when the
        core's scheduler will next play it."""
        service = self._service
        announcements = []
        for announcement in service.list_announcements():
            when = service.next_announcement_run(announcement.id)
            announcements.append({**_dc.asdict(announcement), "next_run": when.isoformat() if when else None})
        message = ProgrammingState(
            header=self._header(), version=self._config_version,
            macros=[_dc.asdict(macro) for macro in service.list_macros()], announcements=announcements,
        )
        return json.loads(message.model_dump_json())

    def _apply_programming(self, request: CoreCommand) -> Optional[dict]:
        """Run a macro or announcement command. Returns a refusal answer, or None if done."""
        service, name, params = self._service, request.command, request.params
        try:
            if name is CommandName.ADD_MACRO:
                try:
                    macro = Macro(**MacroCreateRequest.model_validate(params["macro"]).model_dump())
                except (ValidationError, ValueError, TypeError):
                    return self._command_refused(request, "command.invalid_macro", "that is not a valid macro")
                service.add_macro(macro)
            elif name is CommandName.DELETE_MACRO:
                service.delete_macro(params["pattern"])
            elif name is CommandName.SAVE_ANNOUNCEMENT:
                fields = dict(params["announcement"])
                identifier = fields.pop("id", None)
                try:
                    if not isinstance(identifier, str) or not identifier or len(identifier) > 64:
                        raise ValueError("an announcement needs an id")
                    checked = AnnouncementFields.model_validate(fields)
                except (ValidationError, ValueError, TypeError):
                    return self._command_refused(request, "command.invalid_announcement", "that is not a valid announcement")
                if checked.asset_id and self._assets_have is not None and not self._assets_have(checked.asset_id):
                    return self._command_refused(request, "command.unknown_asset", "no audio clip has that id")
                service.save_announcement(Announcement(id=identifier, **checked.model_dump()))
            elif name is CommandName.DELETE_ANNOUNCEMENT:
                service.delete_announcement(params["announcement_id"])
        except Exception:  # noqa: BLE001 - nothing a client sent may reach the controller as a crash
            _logger.exception("core.command %s could not be applied", name.value)
            return self._command_refused(request, "command.apply_failed", "the core could not do that", retryable=True)
        self._config_changed(None)  # macros and announcements count as a change: clients read them again
        return None

    def _hardware_command(self, request: CoreCommand) -> dict:
        """Act on the radio side's hardware for the API. The core's own checks still apply (an output
        must be set up as one, the transmitter must be on, the number must be allowed), and a refusal
        says why in words the dashboard can show. Nothing here is logged beyond the command's name:
        the number dialled and who asked are the API's audit record, not the core's log."""
        name, params, runtime = request.command, request.params, self._runtime

        def refused(message: str) -> dict:
            return self._command_refused(request, "command.hardware_refused", message)

        try:
            if name is CommandName.GPIO_SET_OUTPUT:
                runtime.gpio.set_output(params["pin"], params["on"])
            elif name is CommandName.REMOTE_BASE_RETUNE:
                runtime.remote_base.retune()
            elif name is CommandName.AUTOPATCH_DIAL:
                error = runtime.autopatch.dial(params["number"], params["actor"])
                if error is not None:
                    return refused(error)
            elif name is CommandName.AUTOPATCH_HANGUP:
                runtime.autopatch.hangup(params["reason"])
        except GpioError as error:
            return refused(str(error))
        except Exception:  # noqa: BLE001 - nothing a client sent may reach the radio side as a crash
            _logger.exception("core.command %s could not be applied", name.value)
            return self._command_refused(request, "command.apply_failed", "the core could not do that", retryable=True)
        _logger.info("core.command %s applied", name.value)
        return self._command_result(request, CommandStatus.APPLIED)

    def _import_snapshot(self, request: CoreCommand) -> dict:
        """Replace the whole configuration, the macros and the announcements (a restore). The
        snapshot is checked first with the dashboard's own validation and the core's own
        safety check, and a refused one applies nothing. It carries credentials and is never
        logged."""
        try:
            data = snapshot_to_import(SnapshotImportRequest.model_validate(request.params["snapshot"]))
        except (ValidationError, ValueError, TypeError):
            return self._command_refused(request, "command.invalid_snapshot", "that is not a valid settings snapshot")
        try:
            _, notes = check_config(config_from_snapshot(data), log=False)
        except CoreConfigError as error:
            return self._command_refused(request, "command.unsafe_snapshot", str(error))
        if notes:
            names = sorted(note.split(" was ", 1)[0] for note in notes)
            return self._command_refused(request, "command.unsafe_snapshot", f"not a usable number: {', '.join(names)}")
        try:
            self._service.import_snapshot(data)
        except Exception:  # noqa: BLE001
            _logger.exception("core.command import_snapshot could not be applied")
            return self._command_refused(request, "command.apply_failed", "the core could not do that", retryable=True)
        _logger.info("core.command import_snapshot applied")
        return self._command_result(request, CommandStatus.APPLIED)

    async def _queue_clip(self, request: CoreCommand) -> dict:
        """Render a clip (off the event loop, with a deadline) and queue it, as the dashboard's
        play button and the API's modules do in process. The clip can be the text of an
        announcement, so it is never logged."""
        clip, on_linked_radio = request.params["clip"], request.params["on_linked_radio"]
        try:
            await asyncio.wait_for(
                self._runtime.render_and_queue(clip, on_linked_radio=on_linked_radio), RENDER_DEADLINE_SECONDS
            )
        except asyncio.TimeoutError:
            return self._command_refused(request, "command.render_timeout", "Preparing the audio took too long.", True)
        except UnknownClipError:
            return self._command_refused(request, "command.unknown_clip", "There is no such clip.")
        except TTSError:
            return self._command_refused(request, "command.render_failed", "The speech could not be prepared.", True)
        except AnnouncementQueueFull:
            return self._command_refused(
                request, "command.queue_full", "Too many announcements are already waiting to play.", True
            )
        except Exception:  # noqa: BLE001
            _logger.exception("core.command queue_clip could not be applied")
            return self._command_refused(request, "command.apply_failed", "the core could not do that", retryable=True)
        _logger.info("core.command queue_clip applied")
        return self._command_result(request, CommandStatus.APPLIED)

    # -- commands (the trusted API channel) -----------------------------------------------------

    def _command_result(self, request: CoreCommand, status: CommandStatus, error: Optional[StructuredError] = None) -> dict:
        message = CommandResult(
            header=self._header(request_id=request.header.message_id, correlation_id=request.header.correlation_id),
            status=status, error=error,
        )
        return json.loads(message.model_dump_json())

    def _command_refused(self, request: CoreCommand, code: str, message: str, retryable: bool = False) -> dict:
        _logger.warning("core.command %s refused (%s)", request.command.value, code)
        return self._command_result(
            request, CommandStatus.REJECTED, StructuredError(code=code, message=message, retryable=retryable)
        )

    async def _command(self, body: bytes) -> dict:
        """`POST /command`: a `core.command` in, a `core.command.result` out. Parameters are
        checked here as well as by the contract; a refused command does nothing."""
        try:
            request = CoreCommand.model_validate_json(body)
        except ValidationError as error:
            problems = [{"at": [str(part) for part in e["loc"]], "type": e["type"]} for e in error.errors()]
            raise _BadRequest(400, "not a valid core.command", problems=problems) from None
        service, name, params = self._service, request.command, request.params
        if name in SIMULATED and not self._allow_simulate:
            return self._command_refused(
                request, "command.simulation_disabled",
                "This core is on a real radio and does not accept simulated events. "
                "Start it with MOREOPENREPEATER_CORE_SIMULATE=1 to allow them.",
            )
        if name is CommandName.QUEUE_CLIP:
            return await self._queue_clip(request)
        if name in HARDWARE:
            return self._hardware_command(request)
        if name is CommandName.IMPORT_SNAPSHOT:
            return self._import_snapshot(request)
        if name in PROGRAMMING:
            refusal = self._apply_programming(request)
            if refusal is not None:
                return refusal
            _logger.info("core.command %s applied", name.value)
            return self._command_result(request, CommandStatus.APPLIED)
        if name is CommandName.CLEAR_LOCKOUT and not service.locked_out:
            return self._command_refused(request, "command.not_locked_out", "The repeater isn't locked out.")
        try:
            if name is CommandName.CLEAR_LOCKOUT:
                service.clear_lockout()
            elif name is CommandName.SET_NET_ACTIVE:
                service.set_net_active(params["active"])
            elif name is CommandName.SIMULATE_COS:
                service.simulate_cos(params["active"])
            elif name is CommandName.SIMULATE_CTCSS:
                service.simulate_ctcss(params["tone_hz"])
            elif name is CommandName.SIMULATE_DTMF:
                service.simulate_dtmf(params["digit"])
            elif name is CommandName.SIMULATE_REMOTE_KEYED:
                service.simulate_remote_keyed(params["node_id"], params["keyed"])
        except Exception:  # noqa: BLE001 - nothing a client sent may reach the controller as a crash
            _logger.exception("core.command %s could not be applied", name.value)
            return self._command_refused(request, "command.apply_failed", "the core could not do that", retryable=True)
        _logger.info("core.command %s applied", name.value)  # names only, never parameters
        return self._command_result(request, CommandStatus.APPLIED)

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


def _handled_families(target: str) -> frozenset:
    """The macro families a client says it handles: `/events?handles=net,metar`. Unknown names are ignored."""
    _, _, query = target.partition("?")
    for part in query.split("&"):
        name, _, value = part.partition("=")
        if name == "handles":
            return frozenset(f for f in value.split(",") if f in MACRO_FAMILIES)
    return frozenset()


def _is_news(sent, new) -> bool:
    """Is `new` different from what the client has, once the client has counted the
    countdowns down on its own? Time between the two is the difference of their
    timestamps, which come from the service's clock."""
    elapsed = max(0.0, new.timestamp - sent.timestamp)
    for name in ("tot_remaining", "hang_remaining", "id_due_in"):
        before, after = getattr(sent, name), getattr(new, name)
        if (before is None) != (after is None):
            return True
        if before is not None and abs(max(0.0, before - elapsed) - after) > COUNTDOWN_TOLERANCE_SECONDS:
            return True
    skip = {"tot_remaining", "hang_remaining", "id_due_in", "timestamp"}
    return any(
        getattr(sent, f.name) != getattr(new, f.name) for f in dataclasses.fields(sent) if f.name not in skip
    )


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


#: GET paths that read their query string themselves, so they are not in `_get`.
_QUERY_PATHS = frozenset({"/logs", "/activity"})


def socket_path_from_env(env: dict[str, str]) -> Optional[Path]:
    """`MOREOPENREPEATER_CORE_SOCKET`; unset means no control socket."""
    value = (env.get("MOREOPENREPEATER_CORE_SOCKET") or "").strip()
    return Path(value) if value else None

