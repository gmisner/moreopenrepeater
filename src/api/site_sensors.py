"""Named analog site sensors: poll, threshold alerts with hysteresis, speech.

Definitions live on the ``sensors`` config facet (``config.sensors.site_sensors``,
ADR 0002 / 0003). Live readings and alert latch state stay in memory; optional
history is SQLite (``data/sensors.db``), same pattern as activity. Hardware
adapters return ``None`` when the device is absent so CI and non-Pi hosts skip
gracefully.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal, Optional

from controller.config.facets.sensors import SiteSensorDef
from playout.renderer import TTS_PREFIX
from sensors.ads1115 import read_ads1115_volts
from sensors.ds18b20 import read_ds18b20_celsius
from sensors.speech import speak_reading

from .notify import Alert
from .persistence import open_database
from .service import RepeaterService

_logger = logging.getLogger("moreopenrepeater.sensors")

POLL_SECONDS_DEFAULT = 30.0
HISTORY_RETENTION_DAYS = 14
HISTORY_FLUSH_SECONDS = 60.0
SensorType = Literal["ds18b20", "ads1115"]
ThresholdSide = Literal["high", "low"]

Reader = Callable[[SiteSensorDef], Optional[float]]


@dataclass(frozen=True)
class SensorReading:
    id: str
    name: str
    type: str
    units: str
    value: Optional[float]
    at: Optional[float]
    high: Optional[float]
    low: Optional[float]
    hysteresis: float
    alert: bool
    speak_on_alert: bool
    alert_state: Optional[ThresholdSide]
    error: Optional[str] = None


class SensorHistoryStore:
    """Append-only reading history. ``path=None`` keeps it in memory (tests)."""

    def __init__(self, path: Optional[Path] = None, clock: Callable[[], float] = time.monotonic) -> None:
        self._conn = open_database(path)
        self._lock = threading.Lock()
        self._clock = clock
        self._pending: list[tuple[str, float, float]] = []
        self._pending_since: Optional[float] = None
        with self._lock, self._conn:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS readings ("
                " sensor_id TEXT NOT NULL, at REAL NOT NULL, value REAL NOT NULL)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS readings_sensor_at ON readings (sensor_id, at)"
            )

    def add(self, sensor_id: str, at: float, value: float) -> None:
        with self._lock:
            self._pending.append((sensor_id, at, value))
            now = self._clock()
            if self._pending_since is None:
                self._pending_since = now
            elif now - self._pending_since >= HISTORY_FLUSH_SECONDS:
                self._flush()

    def flush(self) -> None:
        with self._lock:
            self._flush()

    def _flush(self) -> None:
        if not self._pending:
            return
        with self._conn:
            self._conn.executemany(
                "INSERT INTO readings (sensor_id, at, value) VALUES (?, ?, ?)",
                self._pending,
            )
        self._pending = []
        self._pending_since = None

    def history(self, sensor_id: str, since: float, until: float, limit: int = 500) -> list[dict]:
        with self._lock:
            self._flush()
            rows = self._conn.execute(
                "SELECT at, value FROM readings WHERE sensor_id = ? AND at >= ? AND at < ?"
                " ORDER BY at DESC LIMIT ?",
                (sensor_id, since, until, limit),
            ).fetchall()
        return [{"at": at, "value": value} for at, value in reversed(rows)]

    def prune(self, before: float) -> None:
        with self._lock, self._conn:
            self._flush()
            self._conn.execute("DELETE FROM readings WHERE at < ?", (before,))


def default_reader(sensor: SiteSensorDef) -> Optional[float]:
    if sensor.type == "ds18b20":
        raw = read_ds18b20_celsius(sensor.device or "")
    elif sensor.type == "ads1115":
        raw = read_ads1115_volts(
            sensor.channel,
            bus=sensor.bus,
            address=sensor.address,
        )
    else:
        return None
    if raw is None:
        return None
    return raw * float(sensor.scale) + float(sensor.offset)


class SiteSensors:
    def __init__(
        self,
        service: RepeaterService,
        *,
        reader: Reader = default_reader,
        history: Optional[SensorHistoryStore] = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._service = service
        self._reader = reader
        self.history = history or SensorHistoryStore()
        self._clock = clock
        self._latest: dict[str, SensorReading] = {}
        self._alert_state: dict[str, Optional[ThresholdSide]] = {}
        service.programming.sensor_hook = self._dtmf

    def definitions(self) -> list[SiteSensorDef]:
        return list(self._service.config.sensors.site_sensors)

    def status(self, *, history_hours: float = 0.0) -> dict:
        sensors = []
        now = self._clock()
        for definition in self.definitions():
            sensor_id = definition.id
            reading = self._latest.get(sensor_id)
            row = {
                "id": sensor_id,
                "name": definition.name or sensor_id,
                "type": definition.type,
                "units": definition.units or "",
                "scale": float(definition.scale),
                "offset": float(definition.offset),
                "high": definition.high,
                "low": definition.low,
                "hysteresis": float(definition.hysteresis),
                "alert": bool(definition.alert),
                "speak_on_alert": bool(definition.speak_on_alert),
                "value": None if reading is None else reading.value,
                "at": None if reading is None else reading.at,
                "alert_state": None if reading is None else reading.alert_state,
                "error": None if reading is None else reading.error,
            }
            if history_hours > 0 and sensor_id:
                row["history"] = self.history.history(
                    sensor_id, now - history_hours * 3600, now + 1
                )
            sensors.append(row)
        facet = self._service.config.sensors
        return {
            "enabled": bool(facet.sensors_enabled),
            "poll_interval": float(facet.sensors_poll_interval or POLL_SECONDS_DEFAULT),
            "sensors": sensors,
        }

    def poll(self) -> list[Alert]:
        """Read every configured sensor once. Returns threshold alerts to post."""
        now = self._clock()
        alerts: list[Alert] = []
        seen: set[str] = set()
        for definition in self.definitions():
            sensor_id = (definition.id or "").strip()
            if not sensor_id or sensor_id in seen:
                continue
            seen.add(sensor_id)
            name = (definition.name or sensor_id).strip() or sensor_id
            units = definition.units or ""
            high = definition.high
            low = definition.low
            hysteresis = float(definition.hysteresis)
            alert_on = bool(definition.alert)
            speak_on = bool(definition.speak_on_alert)
            error: Optional[str] = None
            try:
                value = self._reader(definition)
            except Exception as exc:  # noqa: BLE001 — one bad sensor must not stop the rest
                _logger.exception("sensor %s read failed", sensor_id)
                value = None
                error = str(exc) or "read failed"
            if value is None and error is None:
                error = "unavailable"
            if value is not None:
                self.history.add(sensor_id, now, value)
            side = self._alert_state.get(sensor_id)
            new_alerts, side = self._evaluate(
                sensor_id, name, units, value, high, low, hysteresis, side, alert_on
            )
            self._alert_state[sensor_id] = side
            if speak_on:
                for alert in new_alerts:
                    if alert.severity != "info":
                        self._service.speak(TTS_PREFIX + alert.message)
            alerts.extend(new_alerts)
            self._latest[sensor_id] = SensorReading(
                id=sensor_id,
                name=name,
                type=definition.type,
                units=units,
                value=value,
                at=now if value is not None else None,
                high=high,
                low=low,
                hysteresis=hysteresis,
                alert=alert_on,
                speak_on_alert=speak_on,
                alert_state=side,
                error=error if value is None else None,
            )
        stale = [key for key in self._latest if key not in seen]
        for key in stale:
            self._latest.pop(key, None)
            self._alert_state.pop(key, None)
        return alerts

    def _evaluate(
        self,
        sensor_id: str,
        name: str,
        units: str,
        value: Optional[float],
        high: Optional[float],
        low: Optional[float],
        hysteresis: float,
        side: Optional[ThresholdSide],
        alert_on: bool,
    ) -> tuple[list[Alert], Optional[ThresholdSide]]:
        if value is None or not alert_on:
            return [], side
        unit = f" {units}" if units else ""
        alerts: list[Alert] = []
        if side is None:
            if high is not None and value >= high:
                side = "high"
                alerts.append(
                    Alert(
                        f"sensor:{sensor_id}:high",
                        f"{name} high",
                        f"{name} is {value:g}{unit} (high threshold {high:g}{unit}).",
                        "warning",
                    )
                )
            elif low is not None and value <= low:
                side = "low"
                alerts.append(
                    Alert(
                        f"sensor:{sensor_id}:low",
                        f"{name} low",
                        f"{name} is {value:g}{unit} (low threshold {low:g}{unit}).",
                        "warning",
                    )
                )
            return alerts, side
        if side == "high" and high is not None and value < high - hysteresis:
            alerts.append(
                Alert(
                    f"sensor:{sensor_id}:high",
                    f"{name} back to normal",
                    f"{name} is down to {value:g}{unit}.",
                    "info",
                )
            )
            side = None
        elif side == "low" and low is not None and value > low + hysteresis:
            alerts.append(
                Alert(
                    f"sensor:{sensor_id}:low",
                    f"{name} back to normal",
                    f"{name} is up to {value:g}{unit}.",
                    "info",
                )
            )
            side = None
        return alerts, side

    def text(self, sensor_id: str = "") -> str:
        """What the repeater says for one sensor id, or every sensor when blank."""
        definitions = self.definitions()
        if not definitions:
            return "No site sensors are set up."
        wanted = sensor_id.strip().lower()
        if wanted:
            matches = [d for d in definitions if d.id.lower() == wanted]
            if not matches:
                return "That sensor is not set up."
            targets = matches
        else:
            targets = definitions
        parts = []
        for definition in targets:
            name = definition.name or definition.id or "Sensor"
            units = definition.units or ""
            try:
                value = self._reader(definition)
            except Exception:
                _logger.exception("sensor %s read failed during speech", definition.id)
                value = None
            parts.append(speak_reading(name, value, units))
        return " ".join(parts)

    def _dtmf(self, sensor_id: str) -> None:
        async def run() -> None:
            try:
                text = await asyncio.get_running_loop().run_in_executor(None, self.text, sensor_id)
            except Exception:
                _logger.exception("site sensor speech for %r failed", sensor_id)
                text = "Site sensors are not available."
            self._service.speak(TTS_PREFIX + text)

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._service.speak(TTS_PREFIX + self.text(sensor_id))
            return
        loop.create_task(run())
