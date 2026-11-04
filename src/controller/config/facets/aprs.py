from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional
"""AprsConfig: config facet (flat wire field names)."""


@dataclass(frozen=True)
class AprsConfig:
    aprs_enabled: bool = False
    aprs_server: str = 'rotate.aprs2.net'
    aprs_port: int = 14580
    aprs_callsign: str = ''
    aprs_lat: Optional[float] = None
    aprs_lon: Optional[float] = None
    aprs_comment: str = ''
    aprs_beacon_interval: float = 1800.0
    # symbol table + code: "/r" repeater, "/#" digipeater, "/-" house
    aprs_symbol: str = '/r'  # symbol table + code: "/r" repeater, "/#" digipeater, "/-" house
    # Beacon comment prefix like "146.940MHz T100 -060" so radios can tune to us.
    aprs_frequency_mhz: Optional[float] = None
    aprs_offset_mhz: Optional[float] = None
    # None = use require_ctcss_hz
    aprs_tone_hz: Optional[float] = None  # None = use require_ctcss_hz
    # receive nearby stations from APRS-IS for the map
    aprs_map_enabled: bool = False  # receive nearby stations from APRS-IS for the map
    aprs_map_radius_km: float = 50.0
    # how long stations stay on the map
    aprs_map_hours: float = 3.0  # how long stations stay on the map
    # Map background; "" = none (no internet). Any {z}/{x}/{y} tile server works.
    aprs_map_tiles: str = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png'
    distance_units: Literal['mi', 'km'] = 'mi'

# Optional fields the dashboard may null out via clear_<name> on ConfigUpdateRequest.
CLEAR_FIELDS = ('aprs_frequency_mhz', 'aprs_offset_mhz', 'aprs_tone_hz', 'aprs_lat', 'aprs_lon')

