"""APRS map API models."""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel

class AprsStationResponse(BaseModel):
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
    first_heard: float  # unix time
    last_heard: float
    packets: int
    trail: list[list[float]]  # [[lat, lon], ...] oldest first
    distance_km: Optional[float] = None  # from the repeater
    bearing: Optional[int] = None


class AprsMapResponse(BaseModel):
    enabled: bool
    center: Optional[list[float]]  # [lat, lon] of the repeater
    radius_km: float
    hours: float
    tiles: str
    distance_units: Literal["mi", "km"]
    connected: bool
    server: str
    error: Optional[str]
    last_packet: Optional[datetime]
    packets: int
    stations: list[AprsStationResponse]
