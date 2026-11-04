"""Weather alert API models."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel

class WeatherAlertResponse(BaseModel):
    id: str
    event: str
    severity: str
    urgency: str
    headline: str
    area: str
    expires: Optional[str]
    ends: Optional[str]
    announced: bool
    speech: str


class WeatherStatusResponse(BaseModel):
    enabled: bool
    last_checked: Optional[datetime]
    last_error: Optional[str]
    alerts: list[WeatherAlertResponse]
