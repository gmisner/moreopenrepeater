"""The Pi's clock against an NTP server, and the setting for which server (docs/docker.md, The time container)."""
from __future__ import annotations

from typing import Sequence

from fastapi import FastAPI, HTTPException, Request

from ..models.time_sync import TimeServerRequest, TimeStatusResponse
from ..time_sync import TimeServerError, TimeSync


def register(app: FastAPI, *, time_sync: TimeSync, auth_dependencies: Sequence, admin_dependencies: Sequence) -> None:
    @app.get("/api/time", response_model=TimeStatusResponse, dependencies=auth_dependencies)
    async def get_time() -> dict:
        return await time_sync.status()

    @app.put("/api/time/ntp", response_model=TimeStatusResponse, dependencies=admin_dependencies)
    async def put_time_ntp(body: TimeServerRequest, request: Request) -> dict:
        request.state.audit_detail = "NTP server changed"  # that it changed, not to what
        try:
            return await time_sync.set_server(body.server)
        except TimeServerError as error:
            raise HTTPException(status_code=400, detail=str(error)) from None
        except OSError:
            raise HTTPException(status_code=500, detail="The setting could not be saved (the time-config folder is not writable).") from None
