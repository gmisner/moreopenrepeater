"""Simulator request models."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

class SimulateCOSRequest(BaseModel):
    active: bool


class SimulateCTCSSRequest(BaseModel):
    tone_hz: Optional[float] = None


class SimulateDTMFRequest(BaseModel):
    digit: str = Field(min_length=1, max_length=1)


class SimulateRemoteKeyedRequest(BaseModel):
    node_id: str
    keyed: bool
