"""The non-blocking emit path: how a module gets log events out without ever
waiting on a disk, a socket or a collector.

Prototype for Stage 2 (#228). Nothing in production uses it yet. The rules it
demonstrates are written up in docs/contracts/emit-path.md.

    hot path  ->  emit(): a few attribute reads and one deque append
    drain thread  ->  redact, build LogEvent, write JSON lines to sinks

`emit` never raises, never does I/O, never builds a Pydantic object and never
blocks. When the queue is full something is dropped, counted and reported.
"""
from __future__ import annotations

import collections
import socket
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Any, Optional, Protocol

from .common import Sequencer
from .log_event import LogEvent, Severity
from .metrics import Metrics

MAX_DATAGRAM = 8192  # keeps a line well under the smallest platform datagram limit


class Sink(Protocol):
    name: str

    def write(self, lines: list[str]) -> None: ...


class StdoutSink:
    """Layer 1: JSON lines on stdout, which Docker's `local` driver rotates."""

    name = "stdout"

    def write(self, lines: list[str]) -> None:
        sys.stdout.write("\n".join(lines) + "\n")
        sys.stdout.flush()


class ListSink:
    """Collects lines in memory. For tests and the DEBUG ring."""

    name = "list"

    def __init__(self) -> None:
        self.lines: list[str] = []

    def write(self, lines: list[str]) -> None:
        self.lines.extend(lines)


class UnixDatagramSink:
    """Layer 2: one event per datagram to `logd`. Never blocks: if `logd` is
    absent or its socket is full, the event is dropped and the caller counts it."""

    name = "logd"

    def __init__(self, path: str) -> None:
        self.path = path
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self._sock.setblocking(False)

    def write(self, lines: list[str]) -> None:
        failed = 0
        for line in lines:
            data = line.encode()
            if len(data) > MAX_DATAGRAM:
                failed += 1
                continue
            try:
                self._sock.sendto(data, self.path)
            except OSError:
                failed += 1
        if failed:
            raise OSError(f"{failed} of {len(lines)} events not delivered to {self.path}")

    def close(self) -> None:
        self._sock.close()


class Emitter:
    def __init__(
        self,
        module: str,
        version: str,
        sinks: list[Sink],
        *,
        capacity: int = 1000,
        level: Severity = Severity.INFO,
        ring_size: int = 500,
        drain_interval_s: float = 0.1,
        metrics: Optional[Metrics] = None,
    ) -> None:
        self.module = module
        self.version = version
        self.sinks = sinks
        self.capacity = capacity
        self.level = level
        self.drain_interval_s = drain_interval_s
        self.sequencer = Sequencer()
        self.metrics = metrics or Metrics()
        self._queue: collections.deque = collections.deque()
        #: Recent events of every level, including DEBUG, kept in memory only.
        self._ring: collections.deque = collections.deque(maxlen=ring_size)
        self._drop_lock = threading.Lock()  # only taken when the queue is full
        self._unreported_drops: dict[str, int] = {}
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.metrics.counter("mor_log_events_emitted_total", "Events handed to the sinks.", ("severity",))
        self.metrics.counter(
            "mor_log_events_dropped_total", "Events dropped before reaching a sink.", ("reason", "severity")
        )
        self.metrics.counter("mor_log_sink_errors_total", "Failed writes to a sink.", ("sink",))
        self.metrics.gauge("mor_log_queue_depth", "Events waiting to be written.")

    # -- hot path -------------------------------------------------------------

    def emit(
        self,
        severity: Severity,
        event: str,
        *,
        correlation_id: Optional[str] = None,
        error_code: Optional[str] = None,
        message: Optional[str] = None,
        **context: Any,
    ) -> None:
        """Record an event. Safe to call from a controller tick or audio callback:
        no I/O, no locks on the normal path, no exceptions."""
        try:
            record = (
                time.time(), self.sequencer.next(), severity, event, correlation_id, error_code, message, context,
            )
            self._ring.append(record)
            if severity < self.level:
                return
            if len(self._queue) >= self.capacity:
                self._make_room(severity)
                if len(self._queue) >= self.capacity:
                    return  # this event was the one dropped
            self._queue.append(record)
        except Exception:  # noqa: BLE001 - logging must never break the caller
            pass

    def _make_room(self, incoming: Severity) -> None:
        """Queue full. Routine events are dropped; a WARNING or worse evicts the
        oldest routine event instead, so important events survive a flood."""
        with self._drop_lock:
            if incoming >= Severity.WARNING:
                victim = self._pop_oldest_routine()
                if victim is not None:
                    self._count_drop("queue_full", victim[2])
                    return
            self._count_drop("queue_full", incoming)

    def _pop_oldest_routine(self) -> Optional[tuple]:
        try:
            for index, record in enumerate(self._queue):
                if record[2] < Severity.WARNING:
                    del self._queue[index]
                    return record
        except RuntimeError:
            pass  # the drain thread changed the queue under us; drop the new event instead
        return None

    def _count_drop(self, reason: str, severity: Severity) -> None:
        self.metrics.inc("mor_log_events_dropped_total", reason=reason, severity=severity.name)
        key = f"{reason}:{severity.name}"
        self._unreported_drops[key] = self._unreported_drops.get(key, 0) + 1

    # -- control --------------------------------------------------------------

    def set_level(self, level: Severity) -> None:
        """Change what reaches the sinks at runtime. The ring still keeps everything."""
        self.level = level

    def dump_ring(self) -> list[LogEvent]:
        """Recent events of every level, oldest first. For a fault dump or a
        support request."""
        return [self._build(record) for record in list(self._ring)]

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="log-drain", daemon=True)
        self._thread.start()

    def stop(self, timeout_s: float = 2.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout_s)
        self.drain_once()

    # -- drain thread ---------------------------------------------------------

    def _run(self) -> None:
        while not self._stop.is_set():
            if self.drain_once() == 0:  # keep draining while there is work; sleep only when idle
                self._stop.wait(self.drain_interval_s)

    def _build(self, record: tuple) -> LogEvent:
        at, seq, severity, event, correlation_id, error_code, message, context = record
        return LogEvent(
            time=datetime.fromtimestamp(at, timezone.utc), boot_id=self.sequencer.boot_id, seq=seq,
            severity=severity, event=event, module=self.module, module_version=self.version,
            correlation_id=correlation_id, error_code=error_code, message=message, context=context,
        )

    def drain_once(self, limit: int = 500) -> int:
        """Write up to `limit` queued events. Returns how many were taken."""
        taken = []
        while len(taken) < limit:
            try:
                taken.append(self._queue.popleft())
            except IndexError:
                break
        report = self._drop_report()
        if report is not None:
            taken.append(report)  # after the events it describes, so seq order matches write order
        self.metrics.set("mor_log_queue_depth", len(self._queue))
        if not taken:
            return 0
        lines = []
        for record in taken:
            try:
                lines.append(self._build(record).to_json_line())
                self.metrics.inc("mor_log_events_emitted_total", severity=record[2].name)
            except Exception:  # noqa: BLE001 - one bad event must not lose the batch
                self.metrics.inc("mor_log_events_dropped_total", reason="invalid", severity=record[2].name)
        for sink in self.sinks:
            try:
                sink.write(lines)
            except Exception:  # noqa: BLE001 - a dead sink must not stop the others
                self.metrics.inc("mor_log_sink_errors_total", sink=sink.name)
        return len(taken)

    def _drop_report(self) -> Optional[tuple]:
        """A `log.dropped` record for the collector, written straight into the
        batch. It does not go through the queue, so reporting loss never costs
        another event."""
        with self._drop_lock:
            drops, self._unreported_drops = self._unreported_drops, {}
        if not drops:
            return None
        return (
            time.time(), self.sequencer.next(), Severity.WARNING, "log.dropped", None, "log.queue_full", None,
            {"dropped": drops},
        )
