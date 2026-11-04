from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional
"""WeatherConfig: config facet (flat wire field names)."""


@dataclass(frozen=True)
class WeatherConfig:
    wx_alerts_enabled: bool = False
    wx_lat: Optional[float] = None
    wx_lon: Optional[float] = None
    wx_min_severity: Literal['Minor', 'Moderate', 'Severe', 'Extreme'] = 'Severe'
    wx_poll_interval: float = 120.0
    # 0 = announce each alert once
    wx_repeat_minutes: float = 0.0  # 0 = announce each alert once
    # Airports for the "metar" macro: [{"icao": "KAMA", "name": "Amarillo"}]
    metar_airports: list = field(default_factory=list)

# Optional fields the dashboard may null out via clear_<name> on ConfigUpdateRequest.
CLEAR_FIELDS = ('wx_lat', 'wx_lon')

