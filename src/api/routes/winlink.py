"""Winlink RMS gateway status (read-only). No start, stop or restart: the API holds no Docker socket (ADR 0006)."""
from __future__ import annotations

from typing import Sequence

from fastapi import FastAPI

from ..models.winlink import WinlinkStatusResponse
from ..winlink import WinlinkStatus


def register(app: FastAPI, *, winlink: WinlinkStatus, auth_dependencies: Sequence) -> None:
    @app.get("/api/winlink", response_model=WinlinkStatusResponse, dependencies=auth_dependencies)
    async def get_winlink() -> dict:
        return await winlink.status()
