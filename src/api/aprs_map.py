"""APRS stations near the repeater, heard via APRS-IS, for the map.

`AprsReceiver` keeps one receive-only APRS-IS connection open with a range
filter around the repeater, decodes each packet (`link.aprs_packet`) and
records positions in `StationStore`: the latest report per station plus a
trail of where it has moved. Everything older than the map window is
pruned. Needs internet access; repeaters without it simply leave the map
switched off.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Callable, Optional

from controller.state_machine import RepeaterConfig
from link.aprs_packet import AprsPosition, category, parse_packet
from playout.tts import spell_callsign

from .persistence import open_database

_logger = logging.getLogger("moreopenrepeater.aprs")

EARTH_RADIUS_KM = 6371.0
KM_PER_MILE = 1.609344
TRAIL_MIN_MOVE_KM = 0.03  # don't add trail points for GPS jitter
MAX_TRAIL_POINTS = 200
READ_TIMEOUT_SECONDS = 60  # servers send a "#" heartbeat about every 20 s
RECONNECT_SECONDS = (5, 15, 30, 60, 120)
FLUSH_SECONDS = 0.5
# APRS-IS refuses N0CALL-style logins, even receive-only ones.
FALLBACK_LOGIN = "MOREOPEN"
_COMPASS = ("north", "north east", "east", "south east", "south", "south west", "west", "north west")


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def bearing_degrees(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def compass_point(bearing: float) -> str:
    return _COMPASS[int((bearing + 22.5) // 45) % 8]


def map_center(config: RepeaterConfig) -> Optional[tuple[float, float]]:
    """The repeater's location: the APRS beacon position, else the weather one."""
    if config.aprs_lat is not None and config.aprs_lon is not None:
        return config.aprs_lat, config.aprs_lon
    if config.wx_lat is not None and config.wx_lon is not None:
        return config.wx_lat, config.wx_lon
    return None


@dataclass(frozen=True)
class Station:
    name: str
    source: str
    kind: str
    category: str
    lat: float
    lon: float
    symbol_table: str
    symbol_code: str
    comment: str
    course: Optional[int]
    speed_kmh: Optional[float]
    altitude_m: Optional[float]
    weather: Optional[dict]
    first_heard: float
    last_heard: float
    packets: int
    trail: tuple[tuple[float, float, float], ...] = ()  # (at, lat, lon), oldest first


class StationStore:
    """`path=None` keeps everything in memory (tests)."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self._conn = open_database(path)
        self._lock = threading.Lock()
        with self._lock, self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS aprs_station (
                    name TEXT PRIMARY KEY, source TEXT, kind TEXT, category TEXT,
                    lat REAL, lon REAL, symbol_table TEXT, symbol_code TEXT, comment TEXT,
                    course INTEGER, speed_kmh REAL, altitude_m REAL, weather TEXT,
                    first_heard REAL, last_heard REAL, packets INTEGER);
                CREATE INDEX IF NOT EXISTS aprs_station_heard ON aprs_station (last_heard);
                CREATE TABLE IF NOT EXISTS aprs_trail (name TEXT, at REAL, lat REAL, lon REAL);
                CREATE INDEX IF NOT EXISTS aprs_trail_name ON aprs_trail (name, at);
                """
            )

    def record(self, p: AprsPosition, at: float) -> None:
        with self._lock, self._conn:
            if p.killed:
                self._conn.execute("DELETE FROM aprs_station WHERE name = ?", (p.name,))
                self._conn.execute("DELETE FROM aprs_trail WHERE name = ?", (p.name,))
                return
            last = self._conn.execute(
                "SELECT lat, lon FROM aprs_trail WHERE name = ? ORDER BY at DESC LIMIT 1", (p.name,)
            ).fetchone()
            if last is None or distance_km(last[0], last[1], p.lat, p.lon) >= TRAIL_MIN_MOVE_KM:
                self._conn.execute("INSERT INTO aprs_trail VALUES (?, ?, ?, ?)", (p.name, at, p.lat, p.lon))
            self._conn.execute(
                """
                INSERT INTO aprs_station VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT (name) DO UPDATE SET
                    source = excluded.source, kind = excluded.kind, category = excluded.category,
                    lat = excluded.lat, lon = excluded.lon, symbol_table = excluded.symbol_table,
                    symbol_code = excluded.symbol_code,
                    comment = CASE WHEN excluded.comment != '' THEN excluded.comment ELSE comment END,
                    course = excluded.course, speed_kmh = excluded.speed_kmh,
                    altitude_m = COALESCE(excluded.altitude_m, altitude_m),
                    weather = COALESCE(excluded.weather, weather),
                    last_heard = excluded.last_heard, packets = packets + 1
                """,
                (
                    p.name, p.source, p.kind, category(p), p.lat, p.lon, p.symbol_table, p.symbol_code,
                    p.comment, p.course, p.speed_kmh, p.altitude_m,
                    json.dumps(p.weather) if p.weather else None, at, at,
                ),
            )

    def stations(self, since: float) -> list[Station]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM aprs_station WHERE last_heard >= ? ORDER BY last_heard DESC", (since,)
            ).fetchall()
            trails: dict[str, list[tuple[float, float, float]]] = {}
            for name, at, lat, lon in self._conn.execute(
                "SELECT name, at, lat, lon FROM aprs_trail WHERE at >= ? ORDER BY at", (since,)
            ):
                trails.setdefault(name, []).append((at, lat, lon))
        return [
            Station(
                *row[:12],
                weather=json.loads(row[12]) if row[12] else None,
                first_heard=row[13],
                last_heard=row[14],
                packets=row[15],
                trail=tuple(trails.get(row[0], [])[-MAX_TRAIL_POINTS:]),
            )
            for row in rows
        ]

    def prune(self, older_than: float) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM aprs_station WHERE last_heard < ?", (older_than,))
            self._conn.execute("DELETE FROM aprs_trail WHERE at < ?", (older_than,))


def spoken_summary(stations: list[Station], config: RepeaterConfig, now: float, window_hours: float = 1.0) -> str:
    """What the "aprs" DTMF action says."""
    center = map_center(config)
    recent = [s for s in stations if s.last_heard >= now - window_hours * 3600]
    if not recent:
        return "No A P R S stations heard in the last hour."
    count = len(recent)
    parts = [f"{count} A P R S station{'s' if count != 1 else ''} heard in the last hour."]
    moving = sum(1 for s in recent if s.category == "mobile")
    if moving:
        parts.append(f"{moving} {'is' if moving == 1 else 'are'} mobile.")
    if center is not None:
        others = [s for s in recent if s.kind == "station"]
        if others:
            closest = min(others, key=lambda s: distance_km(*center, s.lat, s.lon))
            km = distance_km(*center, closest.lat, closest.lon)
            units = "miles" if config.distance_units == "mi" else "kilometers"
            amount = km / KM_PER_MILE if config.distance_units == "mi" else km
            direction = compass_point(bearing_degrees(*center, closest.lat, closest.lon))
            call = spell_callsign(closest.name.split("-")[0], config.id_phonetic)
            parts.append(f"The closest is {call}, {amount:.0f} {units} {direction}.")
    return " ".join(parts)


# -- receiving ---------------------------------------------------------------


async def aprs_is_lines(host: str, port: int, login: str, filter_: str) -> AsyncIterator[str]:
    """Receive-only APRS-IS session (passcode -1): yields each line."""
    reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=20)
    try:
        writer.write(f"user {login} pass -1 vers moreopenrepeater 0.1 filter {filter_}\r\n".encode())
        await writer.drain()
        while True:
            raw = await asyncio.wait_for(reader.readline(), timeout=READ_TIMEOUT_SECONDS)
            if not raw:
                raise ConnectionError("APRS-IS server closed the connection")
            yield raw.decode("utf-8", "replace")
    finally:
        writer.close()


@dataclass
class ReceiverStatus:
    connected: bool = False
    server: str = ""
    error: Optional[str] = None
    last_packet: Optional[float] = None
    packets: int = 0


class AprsReceiver:
    def __init__(
        self,
        store: StationStore,
        get_config: Callable[[], RepeaterConfig],
        lines: Callable[[str, int, str, str], AsyncIterator[str]] = aprs_is_lines,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.store = store
        self._get_config = get_config
        self._lines = lines
        self._clock = clock
        self.status = ReceiverStatus()
        self._reconnect = asyncio.Event()

    def settings_changed(self) -> None:
        """Call when the config changes; reconnects if the filter differs."""
        self._reconnect.set()

    def _session_key(self, config: RepeaterConfig) -> Optional[tuple]:
        center = map_center(config)
        if not config.aprs_map_enabled or center is None:
            return None
        login = (config.aprs_callsign or config.callsign or FALLBACK_LOGIN).upper()
        filter_ = f"r/{center[0]:.3f}/{center[1]:.3f}/{config.aprs_map_radius_km:g}"
        return config.aprs_server, config.aprs_port, login, filter_

    async def run(self) -> None:
        failures = 0
        while True:
            key = self._session_key(self._get_config())
            if key is None:
                self.status = ReceiverStatus()
                self._reconnect.clear()
                await self._reconnect.wait()
                continue
            host, port, login, filter_ = key
            self._reconnect.clear()
            try:
                await self._session(key)
                failures = 0
            except (OSError, ConnectionError, asyncio.TimeoutError) as error:
                self.status.connected = False
                self.status.error = str(error) or type(error).__name__
                _logger.warning("APRS-IS receive from %s:%s failed: %s", host, port, self.status.error)
                delay = RECONNECT_SECONDS[min(failures, len(RECONNECT_SECONDS) - 1)]
                failures += 1
                try:
                    await asyncio.wait_for(self._reconnect.wait(), timeout=delay)
                except asyncio.TimeoutError:
                    pass

    async def _session(self, key: tuple) -> None:
        host, port, login, filter_ = key
        _logger.info("APRS-IS: receiving %s from %s:%s", filter_, host, port)
        self.status = ReceiverStatus(server=f"{host}:{port}")
        lines = self._lines(host, port, login, filter_)
        pending: list[tuple[AprsPosition, float]] = []
        last_flush = self._clock()
        try:
            async for line in lines:
                if line.startswith("#"):
                    if "not allowed" in line:
                        raise ConnectionError(f"APRS-IS refused the login: {line[1:].strip()}")
                    if "logresp" in line:
                        self.status.connected = True
                        self.status.error = None
                else:
                    self.status.connected = True
                    position = parse_packet(line)
                    if position is not None:
                        now = self._clock()
                        pending.append((position, now))
                        self.status.packets += 1
                        self.status.last_packet = now
                now = self._clock()
                if pending and now - last_flush >= FLUSH_SECONDS:
                    await self._flush(pending)
                    pending, last_flush = [], now
                if self._reconnect.is_set() and self._session_key(self._get_config()) != key:
                    return
                self._reconnect.clear()
        finally:
            if pending:
                await self._flush(pending)
            await lines.aclose()

    async def _flush(self, pending: list[tuple[AprsPosition, float]]) -> None:
        def write() -> None:
            for position, at in pending:
                self.store.record(position, at)

        await asyncio.get_running_loop().run_in_executor(None, write)
