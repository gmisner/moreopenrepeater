"""Run the collector: `python -m logd` or `moreopenrepeater-logd` (#221, #229)."""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

from observability import JsonLineHandler, MaskingFormatter, ProcessIdentity

from .critical import CriticalFile
from .server import Collector, QueryServer
from .store import DEFAULT_CRITICAL_DAYS, DEFAULT_MAX_BYTES, DEFAULT_ROUTINE_DAYS, EventStore

_logger = logging.getLogger("moreopenrepeater.logd")

EVENTS_SOCKET = "MOREOPENREPEATER_LOGD_SOCKET"  # the same name the senders read
QUERY_SOCKET = "MOREOPENREPEATER_LOGD_QUERY_SOCKET"
DATA_DIR = "MOREOPENREPEATER_LOGD_DIR"
MAX_MB = "MOREOPENREPEATER_LOGD_MAX_MB"
ROUTINE_DAYS = "MOREOPENREPEATER_LOGD_ROUTINE_DAYS"
CRITICAL_DAYS = "MOREOPENREPEATER_LOGD_CRITICAL_DAYS"
CRITICAL_DIR = "MOREOPENREPEATER_LOGD_CRITICAL_DIR"  # set when the history is in memory: errors are also kept here


@dataclass(frozen=True)
class Settings:
    events_socket: Path
    query_socket: Path
    data_dir: Path
    max_bytes: int
    routine_days: float
    critical_days: float
    critical_dir: Optional[Path] = None


def _number(env: Mapping[str, str], name: str, default: float, low: float, high: float) -> float:
    """A setting that is a number in range, or the default (and a line in the log saying so)."""
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
        if low <= value <= high:
            return value
    except ValueError:
        pass
    _logger.warning("%s=%r is not a number from %s to %s; using %s", name, raw, low, high, default)
    return default


def settings_from_env(env: Mapping[str, str]) -> Settings:
    routine = _number(env, ROUTINE_DAYS, DEFAULT_ROUTINE_DAYS, 0.01, 3650)
    return Settings(
        events_socket=Path(env.get(EVENTS_SOCKET) or "/run/logd/events.sock"),
        query_socket=Path(env.get(QUERY_SOCKET) or "/run/logd/query.sock"),
        data_dir=Path(env.get(DATA_DIR) or "/var/lib/logd"),
        max_bytes=int(_number(env, MAX_MB, DEFAULT_MAX_BYTES / 1024 / 1024, 1, 100_000) * 1024 * 1024),
        # Errors are never kept for less time than routine events.
        routine_days=routine,
        critical_days=max(routine, _number(env, CRITICAL_DAYS, DEFAULT_CRITICAL_DAYS, 0.01, 3650)),
        critical_dir=Path(env[CRITICAL_DIR].strip()) if (env.get(CRITICAL_DIR) or "").strip() else None,
    )


def open_store(settings: Settings) -> EventStore:
    """The store, or a fresh one if the file is damaged: the damaged file is kept aside, not deleted."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    path = settings.data_dir / "events.db"
    kwargs = dict(max_bytes=settings.max_bytes, routine_days=settings.routine_days, critical_days=settings.critical_days)
    try:
        return EventStore(path, **kwargs)
    except sqlite3.DatabaseError:
        aside = path.with_name(f"events.db.damaged-{int(time.time())}")
        for suffix in ("", "-wal", "-shm"):
            source = Path(str(path) + suffix)
            if source.exists():
                source.rename(Path(str(aside) + suffix))
        _logger.error("the event file was damaged; it is kept as %s and a new one started", aside.name)
        return EventStore(path, **kwargs)


def restore_critical(store: EventStore, critical: CriticalFile) -> int:
    """Put the kept errors back into an empty history, so a restart of the machine does not hide them. A history
    that already has events (the collector alone restarted) is left as it is."""
    if store.stats()["events"]:
        return 0
    try:
        events = critical.load()
        if events:
            store.insert_many(events)
    except Exception as error:  # noqa: BLE001 - a bad file must not stop the collector starting
        _logger.warning("could not restore the critical events: %s", type(error).__name__)
        return 0
    critical.restored = len(events)
    if events:
        _logger.info("restored %d errors from %s", len(events), critical.path)
    return len(events)


async def serve(settings: Settings, stop: Optional[asyncio.Event] = None) -> None:
    stop = stop or asyncio.Event()
    store = open_store(settings)
    critical = CriticalFile(settings.critical_dir) if settings.critical_dir else None
    if critical is not None:
        restore_critical(store, critical)
    collector = Collector(store, settings.events_socket, critical=critical)
    queries = QueryServer(collector, settings.query_socket)
    await collector.start()
    await queries.start()
    _logger.info(
        "logd running: quota %d MB, kept %g days (%g for errors), queries at %s",
        settings.max_bytes // (1024 * 1024), settings.routine_days, settings.critical_days, settings.query_socket,
    )
    await stop.wait()
    await queries.stop()
    await collector.stop()
    store.close()


def configure_logging(env: Mapping[str, str]) -> None:
    """logd's own log: JSON on stdout when asked, text otherwise. It does not send its log to itself."""
    logger = logging.getLogger("moreopenrepeater")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
    if (env.get("MOREOPENREPEATER_LOG_FORMAT") or "").strip().lower() == "json":
        logger.addHandler(JsonLineHandler(ProcessIdentity("logd", "0.1.0")))
    else:
        handler = logging.StreamHandler()
        handler.setFormatter(MaskingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        logger.addHandler(handler)


def main(env: Optional[Mapping[str, str]] = None) -> int:
    environment = dict(os.environ if env is None else env)
    configure_logging(environment)
    settings = settings_from_env(environment)

    async def run() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
        await serve(settings, stop)

    try:
        asyncio.run(run())
    except OSError as error:  # a socket path that cannot be made: say so and stop, rather than loop
        print(f"moreopenrepeater-logd: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
