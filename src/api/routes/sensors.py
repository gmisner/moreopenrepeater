"""Site sensor readings (analog site sensors)."""
from __future__ import annotations

from typing import Sequence

from fastapi import FastAPI, Query

from ..models import SensorsStatusResponse
from ..site_sensors import SiteSensors


def register(
    app: FastAPI,
    *,
    site_sensors: SiteSensors,
    auth_dependencies: Sequence,
) -> None:
    @app.get("/api/sensors", response_model=SensorsStatusResponse, dependencies=auth_dependencies)
    def get_sensors(history_hours: float = Query(default=0, ge=0, le=168)) -> dict:
        return site_sensors.status(history_hours=history_hours)
