"""Macros, scheduled announcements, and NWS weather alerts."""
from __future__ import annotations

import dataclasses
import uuid
from typing import Awaitable, Callable, Sequence

from fastapi import FastAPI, HTTPException
from playout.renderer import UnknownClipError
from playout.tts import TTSError
from wx.nws import alert_areas, speech_text

from controller.announcements import Announcement
from controller.macros import Macro

from ..assets import AudioAssetStore
from ..models import (
    AnnouncementFields,
    AnnouncementResponse,
    MacroCreateRequest,
    MacroResponse,
    StatusResponse,
    WeatherAlertResponse,
    WeatherStatusResponse,
)
from ..service import RepeaterService


def _macro_response(macro: Macro) -> MacroResponse:
    return MacroResponse(**{**dataclasses.asdict(macro), "help_hidden": macro.hidden_from_help})


def _announcement_response(service: RepeaterService, announcement: Announcement) -> AnnouncementResponse:
    return AnnouncementResponse(
        **dataclasses.asdict(announcement), next_run=service.next_announcement_run(announcement.id)
    )


def _weather_response(service: RepeaterService) -> WeatherStatusResponse:
    alerts = [
        WeatherAlertResponse(
            id=a.id, event=a.event, severity=a.severity, urgency=a.urgency, headline=a.headline, area=a.area,
            expires=a.expires, ends=a.ends, announced=service.weather_tracker.was_announced(a.id),
            speech=speech_text(a),
        )
        for a in service.weather_alerts
    ]
    return WeatherStatusResponse(
        enabled=service.config.wx_alerts_enabled,
        last_checked=service.weather_last_checked,
        last_error=service.weather_last_error,
        alerts=alerts,
    )


def register(
    app: FastAPI,
    *,
    service: RepeaterService,
    assets_store: AudioAssetStore,
    zone_shapes: dict,
    render_and_queue: Callable[[str], Awaitable[None]],
    check_weather: Callable[..., Awaitable[None]],
    status_response: Callable[..., StatusResponse],
    auth_dependencies: Sequence,
    transmit_dependencies: Sequence,
) -> None:
    @app.get("/api/macros", response_model=list[MacroResponse], dependencies=auth_dependencies)
    def list_macros() -> list[MacroResponse]:
        return [_macro_response(m) for m in service.list_macros()]

    @app.post("/api/macros", response_model=list[MacroResponse], dependencies=auth_dependencies)
    async def add_macro(body: MacroCreateRequest) -> list[MacroResponse]:
        macro = Macro(**body.model_dump())
        return [_macro_response(m) for m in service.add_macro(macro)]

    @app.delete("/api/macros/{pattern}", response_model=list[MacroResponse], dependencies=auth_dependencies)
    async def delete_macro(pattern: str) -> list[MacroResponse]:
        if pattern not in {m.pattern for m in service.list_macros()}:
            raise HTTPException(status_code=404, detail=f"No macro with pattern {pattern!r}")
        return [_macro_response(m) for m in service.delete_macro(pattern)]

    def find_announcement(announcement_id: str) -> Announcement:
        for announcement in service.list_announcements():
            if announcement.id == announcement_id:
                return announcement
        raise HTTPException(status_code=404, detail=f"No announcement with id {announcement_id!r}")

    def save_announcement(announcement_id: str, body: AnnouncementFields) -> AnnouncementResponse:
        if body.asset_id and not assets_store.has(body.asset_id):
            raise HTTPException(status_code=400, detail=f"No audio clip with id {body.asset_id!r}")
        announcement = Announcement(id=announcement_id, **body.model_dump())
        return _announcement_response(service, service.save_announcement(announcement))

    @app.get("/api/announcements", response_model=list[AnnouncementResponse], dependencies=auth_dependencies)
    def list_announcements() -> list[AnnouncementResponse]:
        return [_announcement_response(service, a) for a in service.list_announcements()]

    @app.post("/api/announcements", response_model=AnnouncementResponse, dependencies=auth_dependencies)
    async def create_announcement(body: AnnouncementFields) -> AnnouncementResponse:
        return save_announcement(uuid.uuid4().hex, body)

    @app.put("/api/announcements/{announcement_id}", response_model=AnnouncementResponse, dependencies=auth_dependencies)
    async def update_announcement(announcement_id: str, body: AnnouncementFields) -> AnnouncementResponse:
        find_announcement(announcement_id)
        return save_announcement(announcement_id, body)

    @app.delete("/api/announcements/{announcement_id}", dependencies=auth_dependencies)
    async def delete_announcement(announcement_id: str) -> dict:
        find_announcement(announcement_id)
        service.delete_announcement(announcement_id)
        return {"deleted": announcement_id}

    @app.post("/api/announcements/{announcement_id}/play", response_model=StatusResponse, dependencies=transmit_dependencies)
    async def play_announcement(announcement_id: str) -> StatusResponse:
        clip = service.announcement_clip(find_announcement(announcement_id))
        try:
            await render_and_queue(clip)
        except UnknownClipError:
            raise HTTPException(status_code=404, detail="The announcement's audio clip no longer exists")
        except TTSError as error:
            raise HTTPException(status_code=503, detail=str(error))
        return status_response(service.snapshot())

    @app.get("/api/weather", response_model=WeatherStatusResponse, dependencies=auth_dependencies)
    def get_weather() -> WeatherStatusResponse:
        return _weather_response(service)

    @app.get("/api/weather/areas", dependencies=auth_dependencies)
    def get_weather_areas() -> dict:
        return alert_areas(service.weather_alerts, zone_shapes)

    @app.post("/api/weather/check", response_model=WeatherStatusResponse, dependencies=auth_dependencies)
    async def check_weather_now() -> WeatherStatusResponse:
        await check_weather(announce=service.config.wx_alerts_enabled)
        return _weather_response(service)

    @app.post("/api/weather/alerts/{alert_id}/play", response_model=StatusResponse, dependencies=transmit_dependencies)
    async def play_weather_alert(alert_id: str) -> StatusResponse:
        alert = next((a for a in service.weather_alerts if a.id == alert_id), None)
        if alert is None:
            raise HTTPException(status_code=404, detail="That alert is no longer active")
        try:
            await render_and_queue(service.weather_alert_clip(alert))
        except TTSError as error:
            raise HTTPException(status_code=503, detail=str(error))
        return status_response(service.snapshot())
