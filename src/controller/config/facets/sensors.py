"""SensorsConfig: named analog site sensors (flat wire field names)."""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any, Literal, Mapping, Optional, Sequence

SensorType = Literal["ds18b20", "ads1115"]


@dataclass(frozen=True)
class SiteSensorDef:
    """One site sensor definition on the config facet (ADR 0003).

    Flat wire / ``/api/config`` still carry a list of plain dicts with the
    same field names; ``RepeaterConfig.to_flat_dict`` / ``from_flat_dict``
    convert at the seam.
    """

    id: str
    name: str
    type: SensorType
    units: str = ""
    scale: float = 1.0
    offset: float = 0.0
    # DS18B20: w1 device id (28-...); blank uses the first sensor found.
    device: str = ""
    # ADS1115:
    bus: int = 1
    address: int = 0x48
    channel: int = 0
    high: Optional[float] = None
    low: Optional[float] = None
    hysteresis: float = 0.5
    alert: bool = True
    speak_on_alert: bool = False

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "SiteSensorDef":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


def coerce_site_sensors(value: object) -> list[SiteSensorDef]:
    """Normalize wire/list/dataclass input into ``list[SiteSensorDef]``."""
    if value is None:
        return []
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise TypeError(f"site_sensors must be a sequence, got {type(value)!r}")
    out: list[SiteSensorDef] = []
    for entry in value:
        if isinstance(entry, SiteSensorDef):
            out.append(entry)
        elif isinstance(entry, Mapping):
            out.append(SiteSensorDef.from_mapping(entry))
        else:
            raise TypeError(f"site sensor entry must be a mapping, got {type(entry)!r}")
    return out


@dataclass(frozen=True)
class SensorsConfig:
    # Poll DS18B20 / ADS1115 sensors in the background (api.site_sensors).
    sensors_enabled: bool = False
    sensors_poll_interval: float = 30.0
    # [{id, name, type, units, scale, offset, device?, bus?, address?, channel?,
    #   high?, low?, hysteresis?, alert?, speak_on_alert?}]
    site_sensors: list[SiteSensorDef] = field(default_factory=list)

    def __post_init__(self) -> None:
        object.__setattr__(self, "site_sensors", coerce_site_sensors(self.site_sensors))


CLEAR_FIELDS: tuple[str, ...] = ()
