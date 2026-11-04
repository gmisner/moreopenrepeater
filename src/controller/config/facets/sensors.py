from __future__ import annotations

from dataclasses import dataclass, field
"""SensorsConfig: named analog site sensors (flat wire field names)."""


@dataclass(frozen=True)
class SensorsConfig:
    # Poll DS18B20 / ADS1115 sensors in the background (api.site_sensors).
    sensors_enabled: bool = False
    sensors_poll_interval: float = 30.0
    # [{id, name, type, units, scale, offset, device?, bus?, address?, channel?,
    #   high?, low?, hysteresis?, alert?, speak_on_alert?}]
    site_sensors: list = field(default_factory=list)

CLEAR_FIELDS: tuple[str, ...] = ()
