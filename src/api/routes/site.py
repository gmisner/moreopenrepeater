"""Logs, APRS map, Home Assistant, METAR, stream, and audit log."""
from __future__ import annotations

import asyncio
import collections
import dataclasses
from datetime import datetime
from pathlib import Path
from typing import Callable, Sequence

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi import Path as UrlPath

from ..airport_weather import AirportWeather
from ..aprs_map import AprsReceiver, StationStore, bearing_degrees, distance_km, map_center
from ..audit import AuditEntry, AuditLog
from ..homeassistant import HomeAssistant, HomeAssistantError
from ..models import (
    AprsMapResponse,
    AprsStationResponse,
    AuditEntryResponse,
    HomeAssistantStatus,
    HomeAssistantTestRequest,
    StreamResponse,
    StreamSettingsRequest,
)
from ..service import RepeaterService
from ..stream import Streamer


def register(
    app: FastAPI,
    *,
    service: RepeaterService,
    log_path: Path,
    aprs_stations: StationStore,
    aprs_receiver: AprsReceiver,
    aprs_window_start: Callable[[], float],
    homeassistant: HomeAssistant,
    airport_weather: AirportWeather,
    streamer: Streamer,
    audit: AuditLog,
    audit_response: Callable[[AuditEntry], AuditEntryResponse],
    actor_of: Callable[[Request], str],
    auth_dependencies: Sequence,
    admin_dependencies: Sequence,
) -> None:
    @app.get("/api/logs", response_model=list[str], dependencies=auth_dependencies)
    def get_logs(lines: int = 200) -> list[str]:
        if not log_path.exists():
            return []
        with log_path.open() as f:
            return [line.rstrip("\n") for line in collections.deque(f, maxlen=lines)]

    @app.get("/api/aprs/stations", response_model=AprsMapResponse, dependencies=auth_dependencies)
    def get_aprs_stations() -> AprsMapResponse:
        config = service.config
        center = map_center(config)
        status = aprs_receiver.status
        stations = []
        for s in aprs_stations.stations(aprs_window_start()):
            fields = dataclasses.asdict(s)
            fields["trail"] = [[lat, lon] for _at, lat, lon in s.trail]
            if center is not None:
                fields["distance_km"] = round(distance_km(*center, s.lat, s.lon), 2)
                fields["bearing"] = round(bearing_degrees(*center, s.lat, s.lon))
            stations.append(AprsStationResponse(**fields))
        return AprsMapResponse(
            enabled=config.aprs_map_enabled,
            center=list(center) if center else None,
            radius_km=config.aprs_map_radius_km,
            hours=config.aprs_map_hours,
            tiles=config.aprs_map_tiles,
            distance_units=config.distance_units,
            connected=status.connected,
            server=status.server,
            error=status.error,
            last_packet=datetime.fromtimestamp(status.last_packet) if status.last_packet else None,
            packets=status.packets,
            stations=stations,
        )

    @app.get("/api/homeassistant", response_model=HomeAssistantStatus, dependencies=auth_dependencies)
    def homeassistant_status() -> HomeAssistantStatus:
        return HomeAssistantStatus(token_set=homeassistant.token_set)

    @app.post("/api/homeassistant/test", dependencies=auth_dependencies)
    async def test_homeassistant(request: Request, body: HomeAssistantTestRequest) -> dict:
        data = {**homeassistant.payload("test", actor_of(request)), "test": True}
        try:
            await asyncio.get_running_loop().run_in_executor(None, homeassistant.send, body.target, data)
        except HomeAssistantError as error:
            raise HTTPException(status_code=502, detail=str(error)) from None
        return {"ok": True}

    @app.get("/api/metar/{icao}", dependencies=auth_dependencies)
    async def metar_preview(icao: str = UrlPath(pattern=r"^[A-Za-z0-9]{4}$")) -> dict:
        text = await asyncio.get_running_loop().run_in_executor(None, airport_weather.text, icao.upper())
        return {"text": text}

    def stream_response() -> StreamResponse:
        return StreamResponse(**streamer.public_settings(), status=streamer.status)

    @app.get("/api/stream", response_model=StreamResponse, dependencies=admin_dependencies)
    def get_stream() -> StreamResponse:
        return stream_response()

    @app.put("/api/stream", response_model=StreamResponse, dependencies=admin_dependencies)
    def put_stream(body: StreamSettingsRequest) -> StreamResponse:
        streamer.update(body.model_dump())
        return stream_response()

    @app.get("/api/audit", response_model=list[AuditEntryResponse], dependencies=admin_dependencies)
    def get_audit(limit: int = Query(default=200, ge=1, le=2000)) -> list[AuditEntryResponse]:
        return [audit_response(e) for e in audit.recent(limit)]
