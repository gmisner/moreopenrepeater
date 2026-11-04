"""Logs, APRS map, Home Assistant, METAR, stream, and audit log."""
from __future__ import annotations

import asyncio
import collections
import dataclasses
import json
import queue
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional, Sequence

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi import Path as UrlPath
from fastapi.responses import StreamingResponse

from ..airport_weather import AirportWeather
from ..aprs_map import AprsReceiver, StationStore, bearing_degrees, distance_km, map_center
from ..audit import AuditEntry, AuditLog
from ..homeassistant import HomeAssistant, HomeAssistantError
from ..log_hub import LEVEL_ORDER, LogHub
from ..models import (
    AprsMapResponse,
    AprsStationResponse,
    AuditEntryResponse,
    HomeAssistantStatus,
    HomeAssistantTestRequest,
    LogLineResponse,
    LogsSinceResponse,
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
    log_hub: LogHub,
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

    @app.get("/api/logs/since", response_model=LogsSinceResponse, dependencies=auth_dependencies)
    def get_logs_since(
        after: int = Query(default=0, ge=0),
        lines: int = Query(default=200, ge=1, le=2000),
    ) -> LogsSinceResponse:
        entries, cursor = log_hub.since(after, lines)
        return LogsSinceResponse(
            lines=[LogLineResponse(seq=e.seq, line=e.line, level=e.level, at=e.at) for e in entries],
            after=cursor,
        )

    @app.get("/api/logs/stream", dependencies=auth_dependencies)
    async def stream_logs(
        after: int = Query(default=0, ge=0),
        level: Optional[str] = Query(default=None),
    ) -> StreamingResponse:
        """Server-sent events of new controller log lines (`data: {"seq","line","level","at"}`)."""
        min_level = (level or "").upper() or None
        if min_level and min_level not in LEVEL_ORDER:
            raise HTTPException(status_code=400, detail="level must be DEBUG, INFO, WARNING, ERROR or CRITICAL")
        min_rank = LEVEL_ORDER.get(min_level or "", 0)
        subscriber = log_hub.subscribe()

        def allowed(entry_level: str) -> bool:
            return not min_level or LEVEL_ORDER.get(entry_level, 0) >= min_rank

        def encode(entry) -> str:
            return f"data: {json.dumps({'seq': entry.seq, 'line': entry.line, 'level': entry.level, 'at': entry.at})}\n\n"

        async def events():
            try:
                backlog, _ = log_hub.since(after, 500)
                for entry in backlog:
                    if allowed(entry.level):
                        yield encode(entry)
                while True:
                    try:
                        entry = await asyncio.get_running_loop().run_in_executor(
                            None, lambda: subscriber.get(timeout=15.0)
                        )
                    except queue.Empty:
                        yield ": keepalive\n\n"
                        continue
                    if entry is None:
                        break
                    if allowed(entry.level):
                        yield encode(entry)
            finally:
                log_hub.unsubscribe(subscriber)

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/aprs/stations", response_model=AprsMapResponse, dependencies=auth_dependencies)
    def get_aprs_stations() -> AprsMapResponse:
        config = service.config
        aprs = config.aprs
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
            enabled=aprs.aprs_map_enabled,
            center=list(center) if center else None,
            radius_km=aprs.aprs_map_radius_km,
            hours=aprs.aprs_map_hours,
            tiles=aprs.aprs_map_tiles,
            distance_units=aprs.distance_units,
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
