"""Winlink RMS gateway status (read-only). No start, stop or restart: the API holds no Docker socket (ADR 0006)."""
from __future__ import annotations

import asyncio
from typing import Sequence

from fastapi import FastAPI

from ..models.winlink import WinlinkStatusResponse
from ..winlink import WinlinkStatus
from ..winlink_activity import WinlinkActivity


def register(app: FastAPI, *, winlink: WinlinkStatus, activity: WinlinkActivity, auth_dependencies: Sequence) -> None:
    @app.get("/api/winlink", response_model=WinlinkStatusResponse, dependencies=auth_dependencies)
    async def get_winlink() -> dict:
        status, sessions = await asyncio.gather(winlink.status(), activity.read())
        return {**status, "activity": sessions}
