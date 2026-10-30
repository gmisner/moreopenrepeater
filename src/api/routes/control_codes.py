"""Per-user TOTP control codes (self-serve and admin list)."""
from __future__ import annotations

from typing import Sequence

import segno
from fastapi import FastAPI, HTTPException, Request

from ..control_codes import ControlCodes, provisioning_uri
from ..models import (
    ControlCodeConfirmRequest,
    ControlCodeSetup,
    ControlCodeStatus,
    ControlCodeUser,
)
from ..service import RepeaterService


def register(
    app: FastAPI,
    *,
    service: RepeaterService,
    control_codes: ControlCodes,
    auth_dependencies: Sequence,
    admin_dependencies: Sequence,
) -> None:
    def code_owner(request: Request) -> str:
        return request.state.identity.username or "local"

    @app.get("/api/me/control-code", response_model=ControlCodeStatus, dependencies=auth_dependencies)
    def my_control_code(request: Request) -> ControlCodeStatus:
        return ControlCodeStatus(**control_codes.status(code_owner(request)))

    @app.post("/api/me/control-code", response_model=ControlCodeSetup, dependencies=auth_dependencies)
    def begin_control_code(request: Request) -> ControlCodeSetup:
        username = code_owner(request)
        secret = control_codes.begin(username)
        uri = provisioning_uri(secret, username, service.config.callsign or "MoreOpenRepeater")
        qr_svg = segno.make(uri, error="m").svg_inline(scale=5, dark="#000", light="#fff")
        return ControlCodeSetup(secret=secret, uri=uri, qr_svg=qr_svg)

    @app.post("/api/me/control-code/confirm", response_model=ControlCodeStatus, dependencies=auth_dependencies)
    def confirm_control_code(request: Request, body: ControlCodeConfirmRequest) -> ControlCodeStatus:
        username = code_owner(request)
        if not control_codes.confirm(username, body.code):
            raise HTTPException(status_code=400, detail="That code doesn't match. Check the phone's clock and try the newest code.")
        return ControlCodeStatus(**control_codes.status(username))

    @app.delete("/api/me/control-code", response_model=ControlCodeStatus, dependencies=auth_dependencies)
    def remove_my_control_code(request: Request) -> ControlCodeStatus:
        username = code_owner(request)
        control_codes.remove(username)
        return ControlCodeStatus(**control_codes.status(username))

    @app.get("/api/control-codes", response_model=list[ControlCodeUser], dependencies=admin_dependencies)
    def list_control_codes() -> list[ControlCodeUser]:
        return [ControlCodeUser(**user) for user in control_codes.enrolled()]

    @app.delete("/api/control-codes/{username}", response_model=list[ControlCodeUser], dependencies=admin_dependencies)
    def remove_control_code(username: str) -> list[ControlCodeUser]:
        if username not in {user["username"] for user in control_codes.enrolled()}:
            raise HTTPException(status_code=404, detail=f"{username!r} hasn't set up one-time codes")
        control_codes.remove(username)
        return list_control_codes()
