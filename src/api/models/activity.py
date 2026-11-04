"""Activity and transmission history API models."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel

class ActivityDay(BaseModel):
    date: str
    rx_seconds: float
    tx_seconds: float
    rx_count: int


class RecordingResponse(BaseModel):
    id: str
    started_at: datetime
    duration: float
    transcript: Optional[str] = None  # None until transcribed
    callsigns: list[str] = []


class ActivitySummaryResponse(BaseModel):
    since: datetime
    until: datetime
    rx_seconds: float
    rx_count: int
    kerchunks: int
    kerchunks_filtered: int
    timeouts: int
    lockouts: int = 0
    longest_rx_seconds: float
    tx_seconds: float
    ids: int
    announcements: int
    by_hour: list[float]
    by_day: list[ActivityDay]


class TransmissionResponse(BaseModel):
    started_at: datetime
    duration: float
    timed_out: bool
