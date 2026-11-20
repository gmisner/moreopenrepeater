"""The Date and time page: which zone the station's clock is on and where that came from."""
from __future__ import annotations

from datetime import datetime
from typing import Sequence

from fastapi import FastAPI
from pydantic import BaseModel

from controller import station_clock

from ..service.core_handle import ApiCoreHandle, CoreUnavailable


class TimezoneResponse(BaseModel):
    #: The saved setting: a zone name, or "" for the container's own.
    setting: str
    #: The zone name in force, or "" if the system cannot name it.
    effective: str
    #: What an empty setting means on this install, and where that came from:
    #: "container" (the TZ the container was started with), "system" (the machine's own files) or "unknown".
    default: str
    default_source: str
    #: This process's own clock right now, with its UTC offset (what announcements and schedules are read from).
    now: datetime
    #: Zone names to pick from. Empty means the image carries no zone data.
    zones: list[str]


def register(app: FastAPI, *, service: ApiCoreHandle, auth_dependencies: Sequence) -> None:
    @app.get("/api/timezone", response_model=TimezoneResponse, dependencies=auth_dependencies)
    def get_timezone() -> TimezoneResponse:
        try:
            setting = service.saved_config.timezone
        except CoreUnavailable:  # the core has not been reached: say what this process is using
            setting = station_clock.applied_timezone()
        default = station_clock.default_zone()
        return TimezoneResponse(
            setting=setting,
            effective=setting or default.name,
            default=default.name,
            default_source=default.source,
            now=datetime.now().astimezone(),
            zones=station_clock.offered_zone_names(),
        )
