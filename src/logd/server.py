"""The collector's two sockets (#221, #229).

Events come in as datagrams on one Unix socket, from every process that logs. They go on a bounded queue and
a writer thread stores them in batches, so a busy moment is one transaction and not hundreds, and the radio's
processes never wait on the disk: if the queue is full an event is dropped and counted, and the count is
recorded as a gap in the store. Searches go out over a second, stream socket, as plain HTTP with JSON, the
same shape as the core's control socket. Nothing here is reachable from the network.
"""
from __future__ import annotations

import asyncio
import collections
import json
import logging
import math
import os
import socket
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from contracts import LogEvent

from .critical import CriticalFile
from .store import MAX_PAGE, EventStore

_logger = logging.getLogger("moreopenrepeater.logd")

MAX_DATAGRAM = 64 * 1024
QUEUE_SIZE = 5000
BATCH_SIZE = 500
FLUSH_SECONDS = 2.0
PRUNE_SECONDS = 60.0
RECEIVE_BURST = 256  # datagrams taken in one go before the loop gets its turn back
REQUEST_DEADLINE_SECONDS = 10.0
MAX_REQUEST_LINE = 8192
MAX_HEADERS = 50
MAX_CONNECTIONS = 8
MAX_TEXT = 200
MAX_NAME = 64
_SEVERITIES = {"debug": 10, "info": 20, "warning": 30, "error": 40, "critical": 50}
_REASONS = {200: "OK", 400: "Bad Request", 404: "Not Found", 405: "Method Not Allowed", 408: "Request Timeout",
            503: "Service Unavailable"}


class Collector:
    def __init__(
        self,
        store: EventStore,
        events_socket: Path,
        *,
        queue_size: int = QUEUE_SIZE,
        batch_size: int = BATCH_SIZE,
        flush_seconds: float = FLUSH_SECONDS,
        prune_seconds: float = PRUNE_SECONDS,
        max_datagram: int = MAX_DATAGRAM,
        critical: Optional[CriticalFile] = None,
    ) -> None:
        self.store = store
        self.critical = critical  # errors are also kept here when the main history is in memory
        self.path = events_socket
        self.queue_size, self.batch_size = queue_size, batch_size
        self.flush_seconds, self.prune_seconds, self.max_datagram = flush_seconds, prune_seconds, max_datagram
        self._queue: collections.deque[LogEvent] = collections.deque()
        self._sock: Optional[socket.socket] = None
        self._wake: Optional[asyncio.Event] = None
        self._tasks: list[asyncio.Task] = []
        self.received = self.rejected = self.written = 0
        self.dropped_queue = self.dropped_write = self.write_errors = 0
        self.last_rejected: Optional[str] = None
        self._overflow_recorded = 0

    # -- lifecycle -----------------------------------------------------------------------------------------

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
        self.path.unlink(missing_ok=True)  # a socket left by a crash, or anything else in the way
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        previous = os.umask(0o117)  # created 0660 whatever the umask: the processes that log share a group
        try:
            sock.bind(str(self.path))
        finally:
            os.umask(previous)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)  # room for a burst
        except OSError:
            pass
        sock.setblocking(False)
        self._sock = sock
        self._wake = asyncio.Event()
        loop.add_reader(sock.fileno(), self._on_readable)
        self._tasks = [asyncio.create_task(self._writer()), asyncio.create_task(self._housekeeping())]
        _logger.info("collecting events at %s", self.path)

    async def stop(self) -> None:
        loop = asyncio.get_running_loop()
        if self._sock is not None:
            loop.remove_reader(self._sock.fileno())
            self._on_readable()  # whatever is already waiting in the socket
            self._sock.close()
            self._sock = None
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        await self._drain()  # what is still queued is written, not lost with the process
        self.path.unlink(missing_ok=True)

    # -- events in -----------------------------------------------------------------------------------------

    def _on_readable(self) -> None:
        sock = self._sock
        if sock is None:
            return
        for _ in range(RECEIVE_BURST):
            try:
                data = sock.recv(self.max_datagram + 1)
            except BlockingIOError:
                break
            except OSError:
                break
            self.received += 1
            try:
                if len(data) > self.max_datagram:
                    raise ValueError("a datagram larger than the limit")
                event = LogEvent.model_validate(json.loads(data))
            except Exception as error:  # noqa: BLE001 - anything that is not an event is counted, not trusted
                self.rejected += 1
                self.last_rejected = f"{type(error).__name__}: {str(error).splitlines()[0][:120]}"
                continue
            if len(self._queue) >= self.queue_size:
                self.dropped_queue += 1
                continue
            self._queue.append(event)
        if self._wake is not None and len(self._queue) >= self.batch_size:
            self._wake.set()

    # -- events stored -------------------------------------------------------------------------------------

    async def _writer(self) -> None:
        assert self._wake is not None
        while True:
            try:
                await asyncio.wait_for(self._wake.wait(), self.flush_seconds)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()
            await self._drain()

    async def _drain(self) -> None:
        loop = asyncio.get_running_loop()
        while self._queue:
            batch = [self._queue.popleft() for _ in range(min(self.batch_size, len(self._queue)))]
            if self.critical is not None:  # its own try: a failed store write must not lose the errors
                await loop.run_in_executor(None, self.critical.append, batch)
            try:
                await loop.run_in_executor(None, self.store.insert_many, batch)
                self.written += len(batch)
            except Exception as error:  # noqa: BLE001 - a full disk must not stop the collector
                self.write_errors += 1
                self.dropped_write += len(batch)
                _logger.warning("could not store %d events: %s", len(batch), type(error).__name__)
        await self._record_overflow(loop)

    async def _record_overflow(self, loop: asyncio.AbstractEventLoop) -> None:
        count = self.dropped_queue - self._overflow_recorded
        if count > 0:
            try:
                await loop.run_in_executor(
                    None, self.store.note_gap, "collector_overflow", "logd", None, count, "the collector's queue was full"
                )
                self._overflow_recorded += count
            except Exception:  # noqa: BLE001
                pass

    async def _housekeeping(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            await asyncio.sleep(self.prune_seconds)
            try:
                removed = await loop.run_in_executor(None, self.store.prune)
                if removed["retention"] or removed["quota"]:
                    _logger.info("pruned %d events past retention and %d over the quota", removed["retention"], removed["quota"])
            except Exception as error:  # noqa: BLE001
                _logger.warning("pruning failed: %s", type(error).__name__)

    def stats(self) -> dict[str, Any]:
        return {
            "received": self.received, "rejected": self.rejected, "last_rejected": self.last_rejected,
            "written": self.written, "dropped_queue": self.dropped_queue, "dropped_write": self.dropped_write,
            "write_errors": self.write_errors, "queue": len(self._queue), "queue_size": self.queue_size,
        }

    def health(self) -> str:
        """`degraded` when it is losing events right now; there is no `failed`, a collector that answers is up."""
        if self.write_errors and self.dropped_write:
            return "degraded"
        return "degraded" if len(self._queue) >= self.queue_size * 0.8 else "ok"


class _BadRequest(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status, self.message = status, message


def _text(query: dict[str, list[str]], name: str, limit: int) -> Optional[str]:
    value = query.get(name, [None])[-1]
    if value is None or value == "":
        return None
    if len(value) > limit:
        raise _BadRequest(400, f"{name} may be at most {limit} characters")
    return value


def _integer(query: dict[str, list[str]], name: str, low: int = 0, high: int = 2**62) -> Optional[int]:
    value = query.get(name, [None])[-1]
    if value is None or value == "":
        return None
    try:
        number = int(value)
    except ValueError:
        raise _BadRequest(400, f"{name} must be a whole number") from None
    if not low <= number <= high:
        raise _BadRequest(400, f"{name} must be from {low} to {high}")
    return number


def _moment(query: dict[str, list[str]], name: str) -> Optional[float]:
    value = query.get(name, [None])[-1]
    if value is None or value == "":
        return None
    try:
        seconds = float(value)
        if not math.isfinite(seconds):
            raise ValueError
        return seconds
    except ValueError:
        pass
    try:
        when = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise _BadRequest(400, f"{name} must be seconds since 1970 or an ISO 8601 time") from None
    return (when if when.tzinfo else when.replace(tzinfo=timezone.utc)).timestamp()


def _severity(query: dict[str, list[str]]) -> Optional[int]:
    value = query.get("min_severity", [None])[-1]
    if value is None or value == "":
        return None
    if value.lower() in _SEVERITIES:
        return _SEVERITIES[value.lower()]
    try:
        return int(value)
    except ValueError:
        raise _BadRequest(400, "min_severity is debug, info, warning, error, critical, or a number") from None


class QueryServer:
    def __init__(self, collector: Collector, path: Path) -> None:
        self.collector = collector
        self.store = collector.store
        self.path = path
        self._server: Optional[asyncio.AbstractServer] = None
        self._open = 0

    async def start(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
        self.path.unlink(missing_ok=True)
        previous = os.umask(0o117)
        try:
            self._server = await asyncio.start_unix_server(self._connection, path=str(self.path), limit=MAX_REQUEST_LINE * 2)
        finally:
            os.umask(previous)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            try:
                await asyncio.wait_for(self._server.wait_closed(), 2.0)
            except asyncio.TimeoutError:
                pass
            self._server = None
        self.path.unlink(missing_ok=True)

    async def _connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if self._open >= MAX_CONNECTIONS:
            await self._respond(writer, 503, {"error": "too many connections"})
            return
        self._open += 1
        try:
            try:
                method, target = await asyncio.wait_for(self._read_request(reader), REQUEST_DEADLINE_SECONDS)
                if method != "GET":
                    raise _BadRequest(405, "use GET")
                status, body = await self._dispatch(target)
            except asyncio.TimeoutError:
                status, body = 408, {"error": "request timed out"}
            except _BadRequest as error:
                status, body = error.status, {"error": error.message}
            await self._respond(writer, status, body)
        except (ConnectionError, OSError):
            pass
        except Exception:  # noqa: BLE001 - one connection's bug must never reach the collector
            _logger.exception("query socket: unexpected error")
        finally:
            self._open -= 1
            try:
                writer.close()
            except Exception:  # noqa: BLE001
                pass

    async def _read_request(self, reader: asyncio.StreamReader) -> tuple[str, str]:
        try:
            line = await reader.readuntil(b"\r\n")
        except (asyncio.LimitOverrunError, asyncio.IncompleteReadError):
            raise _BadRequest(400, "bad request line") from None
        if len(line) > MAX_REQUEST_LINE:
            raise _BadRequest(400, "request line too long")
        parts = line.decode("latin-1").rstrip("\r\n").split(" ")
        if len(parts) != 3 or not parts[2].startswith("HTTP/1."):
            raise _BadRequest(400, "malformed request line")
        for _ in range(MAX_HEADERS + 1):
            try:
                header = await reader.readuntil(b"\r\n")
            except (asyncio.LimitOverrunError, asyncio.IncompleteReadError):
                raise _BadRequest(400, "malformed headers") from None
            if header == b"\r\n":
                return parts[0], parts[1]
        raise _BadRequest(400, "too many headers")

    async def _dispatch(self, target: str) -> tuple[int, dict]:
        split = urllib.parse.urlsplit(target)
        query = urllib.parse.parse_qs(split.query, keep_blank_values=True)
        loop = asyncio.get_running_loop()
        if split.path == "/health":
            return 200, {"state": self.collector.health(), "queue": len(self.collector._queue)}
        if split.path == "/stats":
            stats = await self._store(loop, self.store.stats)
            critical = self.collector.critical
            return 200, {
                **stats, "collector": self.collector.stats(),
                # True when the history is in memory and only errors survive a restart of the machine
                "volatile": critical is not None, "critical_file": critical.stats() if critical else None,
            }
        if split.path == "/gaps":
            limit = _integer(query, "limit", 1, MAX_PAGE) or 100
            return 200, {"gaps": await self._store(loop, lambda: self.store.gaps(limit))}
        if split.path == "/events":
            kwargs: dict[str, Any] = {
                "since": _moment(query, "since"), "until": _moment(query, "until"), "min_severity": _severity(query),
                "module": _text(query, "module", MAX_NAME), "event": _text(query, "event", 80),
                "correlation_id": _text(query, "correlation_id", MAX_NAME), "q": _text(query, "q", MAX_TEXT),
                "before": _integer(query, "before"), "limit": _integer(query, "limit", 1, MAX_PAGE) or 200,
            }
            return 200, await self._store(loop, lambda: self.store.query(**kwargs))
        raise _BadRequest(404, f"no such path: {split.path}")

    @staticmethod
    async def _store(loop: asyncio.AbstractEventLoop, call) -> Any:
        try:
            return await loop.run_in_executor(None, call)
        except Exception as error:  # noqa: BLE001 - a store that cannot answer is an answer, not a crash
            _logger.warning("the store could not answer a query: %s", type(error).__name__)
            raise _BadRequest(503, "the store could not answer") from None

    async def _respond(self, writer: asyncio.StreamWriter, status: int, body: dict) -> None:
        payload = json.dumps(body, separators=(",", ":")).encode()
        head = (
            f"HTTP/1.1 {status} {_REASONS.get(status, 'Error')}\r\nContent-Type: application/json\r\n"
            f"Content-Length: {len(payload)}\r\nCache-Control: no-store\r\nConnection: close\r\n"
        )
        if status == 405:
            head += "Allow: GET\r\n"
        writer.write(head.encode() + b"\r\n" + payload)
        await asyncio.wait_for(writer.drain(), 5.0)
