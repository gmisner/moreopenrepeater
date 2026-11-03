"""Live log fan-out: ring buffer, SSE subscribers, and optional remote shipping.

Controller logs stay on the Pi by default. An admin can also ship them to an
HTTPS URL (the person helping them pastes one in) so a remote agent can follow
a repeater it can't reach. Secrets live in their own data file, like alerts.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import queue
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass
from typing import Callable, Deque, Iterator, Optional

from .persistence import StateStore

_logger = logging.getLogger("moreopenrepeater.log_hub")

RING_SIZE = 2000
SHIP_TIMEOUT_SECONDS = 10.0
LEVEL_ORDER = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}
SECRET_FIELDS = ("token", "url")


@dataclass(frozen=True)
class LogLine:
    seq: int
    line: str
    level: str
    at: float


@dataclass(frozen=True)
class LogShippingSettings:
    enabled: bool = False
    url: str = ""  # HTTPS ingest the helper owns; Bearer token optional
    token: str = ""
    min_level: str = "INFO"
    batch_seconds: float = 2.0
    batch_lines: int = 50
    callsign: str = ""  # stamped on each batch; set from the live config


_SETTINGS_FIELDS = {f.name for f in dataclasses.fields(LogShippingSettings)}


class LogHub:
    """Thread-safe ring of recent log lines plus live subscriber queues."""

    def __init__(self, capacity: int = RING_SIZE, clock: Callable[[], float] = time.time) -> None:
        self._capacity = capacity
        self._clock = clock
        self._lock = threading.Lock()
        self._seq = 0
        self._ring: Deque[LogLine] = deque(maxlen=capacity)
        self._subscribers: list[queue.Queue[Optional[LogLine]]] = []
        self._ship_queue: queue.Queue[Optional[LogLine]] = queue.Queue(maxsize=capacity * 2)
        self.on_publish: Optional[Callable[[LogLine], None]] = None

    @property
    def cursor(self) -> int:
        with self._lock:
            return self._seq

    def publish(self, line: str, level: str = "INFO") -> LogLine:
        with self._lock:
            self._seq += 1
            entry = LogLine(seq=self._seq, line=line, level=level.upper(), at=self._clock())
            self._ring.append(entry)
            subscribers = list(self._subscribers)
        for subscriber in subscribers:
            try:
                subscriber.put_nowait(entry)
            except queue.Full:
                pass
        try:
            self._ship_queue.put_nowait(entry)
        except queue.Full:
            pass
        if self.on_publish is not None:
            try:
                self.on_publish(entry)
            except Exception:  # noqa: BLE001 -- a bad hook must not break logging
                _logger.exception("log hub on_publish failed")
        return entry

    def since(self, after: int = 0, limit: int = 500) -> tuple[list[LogLine], int]:
        limit = max(1, min(limit, self._capacity))
        with self._lock:
            lines = [entry for entry in self._ring if entry.seq > after][-limit:]
            cursor = self._seq
        return lines, cursor

    def subscribe(self, maxsize: int = 500) -> queue.Queue[Optional[LogLine]]:
        subscriber: queue.Queue[Optional[LogLine]] = queue.Queue(maxsize=maxsize)
        with self._lock:
            self._subscribers.append(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: queue.Queue[Optional[LogLine]]) -> None:
        with self._lock:
            try:
                self._subscribers.remove(subscriber)
            except ValueError:
                pass
        try:
            subscriber.put_nowait(None)
        except queue.Full:
            pass

    def wake_shipper(self) -> None:
        try:
            self._ship_queue.put_nowait(None)
        except queue.Full:
            pass

    def iter_ship_batch(self, max_lines: int, wait_seconds: float) -> Iterator[LogLine]:
        """Block for up to wait_seconds, then yield up to max_lines queued for shipping."""
        limit = max(1, max_lines)
        deadline = time.monotonic() + max(0.1, wait_seconds)
        try:
            first = self._ship_queue.get(timeout=max(0.0, deadline - time.monotonic()))
        except queue.Empty:
            return
        if first is None:
            return
        yield first
        for _ in range(limit - 1):
            try:
                item = self._ship_queue.get_nowait()
            except queue.Empty:
                break
            if item is None:
                break
            yield item


class HubHandler(logging.Handler):
    """Formats records and pushes them into a LogHub."""

    def __init__(self, hub: LogHub) -> None:
        super().__init__()
        self.hub = hub

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.hub.publish(self.format(record), level=record.levelname)
        except Exception:  # noqa: BLE001 -- logging must never raise into the app
            self.handleError(record)


def _level_at_least(level: str, minimum: str) -> bool:
    return LEVEL_ORDER.get(level.upper(), 0) >= LEVEL_ORDER.get(minimum.upper(), 0)


def _post_batch(url: str, token: str, callsign: str, lines: list[LogLine], timeout: float) -> None:
    """POST one JSON array of events (Axiom ingest shape; other sinks accept it too)."""
    body = [
        {
            "_time": entry.at,
            "seq": entry.seq,
            "level": entry.level,
            "message": entry.line,
            "callsign": callsign,
            "source": "moreopenrepeater",
        }
        for entry in lines
    ]
    headers = {"Content-Type": "application/json", "User-Agent": "moreopenrepeater"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        response.read()


def describe_shipping_error(error: Exception) -> str:
    """Never echo the URL: ingest URLs often embed tokens."""
    reason = getattr(error, "reason", None)
    if isinstance(error, urllib.error.HTTPError):
        return f"HTTP {error.code} {error.reason}"
    if reason is not None:
        error = reason if isinstance(reason, Exception) else Exception(reason)
    if isinstance(error, OSError) and error.strerror:
        return error.strerror
    return str(error) or type(error).__name__


class LogShipper:
    """Batches hub lines to an HTTPS ingest URL the helper owns."""

    def __init__(
        self,
        hub: LogHub,
        store: Optional[StateStore] = None,
        post: Callable[[str, str, str, list[LogLine], float], None] = _post_batch,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._hub = hub
        self._store = store
        self._post = post
        self._clock = clock
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.last_error: str = ""
        self.last_sent_at: Optional[float] = None
        self.lines_sent: int = 0
        saved = store.load() if store is not None else None
        fields = {k: v for k, v in (saved or {}).items() if k in _SETTINGS_FIELDS}
        if "min_level" in fields and str(fields["min_level"]).upper() not in LEVEL_ORDER:
            fields["min_level"] = "INFO"
        self.settings = LogShippingSettings(**fields)

    @property
    def active(self) -> bool:
        settings = self.settings
        return bool(settings.enabled and settings.url)

    def update(self, **changes: object) -> LogShippingSettings:
        with self._lock:
            cleaned = dict(changes)
            if "min_level" in cleaned and cleaned["min_level"] is not None:
                level = str(cleaned["min_level"]).upper()
                if level not in LEVEL_ORDER:
                    raise ValueError("min_level must be DEBUG, INFO, WARNING, ERROR or CRITICAL")
                cleaned["min_level"] = level
            self.settings = dataclasses.replace(self.settings, **cleaned)
            if self._store is not None:
                self._store.save(dataclasses.asdict(self.settings))
            settings = self.settings
        if settings.enabled and settings.url:
            self.start()
        return settings

    def set_callsign(self, callsign: str) -> None:
        with self._lock:
            if self.settings.callsign == callsign:
                return
            self.settings = dataclasses.replace(self.settings, callsign=callsign)
            if self._store is not None:
                self._store.save(dataclasses.asdict(self.settings))

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="log-shipper", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._hub.wake_shipper()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)

    def send_test(self) -> Optional[str]:
        """POST one synthetic line; returns an error string or None on success."""
        settings = self.settings
        if not settings.url:
            return "Set an ingest URL first"
        entry = LogLine(seq=0, line="moreopenrepeater remote logging test", level="INFO", at=self._clock())
        try:
            self._post(settings.url, settings.token, settings.callsign, [entry], SHIP_TIMEOUT_SECONDS)
        except Exception as error:  # noqa: BLE001
            message = describe_shipping_error(error)
            self.last_error = message
            return message
        self.last_error = ""
        self.last_sent_at = self._clock()
        return None

    def _run(self) -> None:
        while not self._stop.is_set():
            settings = self.settings
            if not (settings.enabled and settings.url):
                if self._stop.wait(1.0):
                    break
                continue
            batch = [
                entry
                for entry in self._hub.iter_ship_batch(settings.batch_lines, settings.batch_seconds)
                if _level_at_least(entry.level, settings.min_level)
            ]
            if not batch:
                continue
            try:
                self._post(settings.url, settings.token, settings.callsign, batch, SHIP_TIMEOUT_SECONDS)
            except Exception as error:  # noqa: BLE001 -- keep shipping after a bad POST
                self.last_error = describe_shipping_error(error)
                _logger.warning("couldn't ship log batch: %s", self.last_error)
                time.sleep(min(30.0, settings.batch_seconds * 5))
                continue
            self.last_error = ""
            self.last_sent_at = self._clock()
            self.lines_sent += len(batch)
