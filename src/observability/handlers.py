"""Handlers that send structured events out of a process (#221, #229).

Two ways out. `JsonLineHandler` writes one JSON object per line to stdout, which is what a container
runtime's own log driver keeps and rotates, and works with every other part of the system down.
`CollectorHandler` sends each event to the `logd` collector as a datagram on a Unix socket.

The second one is held to a rule from the architecture: logging must never hold up the radio. So the
code that logs pays only for a small snapshot and a put on a bounded queue; a separate thread builds the
event and does the sending; a collector that is absent, slow or full means the event is dropped, never that
anything waits. Every drop is counted, and when the collector is next reachable it is told how many events
it missed (a `logging.dropped` event), so a gap in the record is reported instead of hidden.
"""
from __future__ import annotations

import logging
import queue
import socket
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Optional

from contracts import LogEvent, Severity

from .events import ProcessIdentity, RecordSnapshot, record_to_event, snapshot, stamp

MAX_DATAGRAM = 60_000  # well under what a Unix datagram socket takes (about 200 KB by default)
QUEUE_SIZE = 1000
_STOP = object()


class JsonLineHandler(logging.Handler):
    """One JSON event per line on a stream (stdout, by default)."""

    def __init__(self, identity: ProcessIdentity, stream: Optional[IO[str]] = None) -> None:
        super().__init__()
        self._identity = identity
        self._stream = stream

    def emit(self, record: logging.LogRecord) -> None:
        try:
            event = record_to_event(snapshot(record), self._identity, stamp(record, self._identity))
            stream = self._stream or sys.stdout
            stream.write(event.to_json_line() + "\n")
            stream.flush()
        except Exception:  # noqa: BLE001
            self.handleError(record)


class CollectorHandler(logging.Handler):
    def __init__(
        self,
        identity: ProcessIdentity,
        socket_path: Path,
        *,
        queue_size: int = QUEUE_SIZE,
        max_datagram: int = MAX_DATAGRAM,
    ) -> None:
        super().__init__()
        self._identity = identity
        self._path = str(socket_path)
        self._max = max_datagram
        self._queue: queue.Queue = queue.Queue(maxsize=queue_size)
        self._counts = threading.Lock()
        self.dropped_total = 0
        self._unreported = 0
        self._since: Optional[datetime] = None
        self._socket: Optional[socket.socket] = None
        self._closed = False
        self._thread = threading.Thread(target=self._run, name="log-collector-sender", daemon=True)
        self._thread.start()

    @property
    def running(self) -> bool:
        return self._thread.is_alive()

    # -- the logging thread: cheap, and never waits ------------------------------------------------

    def emit(self, record: logging.LogRecord) -> None:
        if self._closed:
            return
        try:
            self._queue.put_nowait((snapshot(record), stamp(record, self._identity)))
        except queue.Full:
            self._drop()
        except Exception:  # noqa: BLE001
            self.handleError(record)

    def _drop(self, count: int = 1) -> None:
        with self._counts:
            self.dropped_total += count
            self._unreported += count
            if self._since is None:
                self._since = datetime.now(timezone.utc)

    # -- the sender's own thread -------------------------------------------------------------------

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is _STOP:
                return
            snap, seq = item
            try:
                data = self._build(snap, seq)
                if len(data) > self._max:
                    self._drop()
                elif self._send(data):
                    self._report_gap()
                else:
                    self._drop()
            except Exception:  # noqa: BLE001 - nothing here may end the sender
                self._drop()

    def _build(self, snap: RecordSnapshot, seq: int) -> bytes:
        return record_to_event(snap, self._identity, seq).to_json_line().encode()

    def _send(self, data: bytes) -> bool:
        """One datagram, without waiting. False when the collector is not there or cannot take it now."""
        if self._socket is None:
            self._socket = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            self._socket.setblocking(False)
        try:
            self._socket.sendto(data, self._path)
            return True
        except OSError:  # no such socket, refused, or its buffer is full
            return False

    def _report_gap(self) -> None:
        """Tell the collector how many events it did not get, now that it can hear."""
        with self._counts:
            count, since = self._unreported, self._since
        if not count:
            return
        gap = LogEvent(
            time=datetime.now(timezone.utc), boot_id=self._identity.sequencer.boot_id,
            seq=self._identity.sequencer.next(), severity=Severity.WARNING, event="logging.dropped",
            module=self._identity.module, module_version=self._identity.version,
            message="events were dropped before they reached the collector",
            context={"dropped": count, "since": since.isoformat() if since else None},
        )
        if self._send(gap.to_json_line().encode()):
            with self._counts:
                self._unreported -= count
                if self._unreported == 0:
                    self._since = None

    # -- shutdown ----------------------------------------------------------------------------------

    def close(self) -> None:
        if not self._closed:
            self._closed = True  # no more events are taken; what is queued is still sent
            try:
                self._queue.put(_STOP, timeout=1)
            except queue.Full:
                pass
            self._thread.join(3)
            if self._socket is not None:
                self._socket.close()
                self._socket = None
        super().close()
