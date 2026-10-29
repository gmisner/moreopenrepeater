"""Phone-patch status, dial/hangup, and SIP trunk setup."""
from __future__ import annotations

from typing import Awaitable, Sequence

from fastapi import FastAPI, HTTPException, Request

from ..autopatch import Autopatch
from ..models import (
    AutopatchDialRequest,
    AutopatchStatusResponse,
    SipTrunkRequest,
    SipTrunkStatusResponse,
)
from ..service import RepeaterService
from ..sip_trunk import DIAL_STRING as TRUNK_DIAL_STRING
from ..sip_trunk import AsteriskSetupError, SipTrunk, TrunkSettings


def register(
    app: FastAPI,
    *,
    service: RepeaterService,
    autopatch: Autopatch,
    sip_trunk: SipTrunk,
    auth_dependencies: Sequence,
    admin_dependencies: Sequence,
    transmit_dependencies: Sequence,
) -> None:
    @app.get("/api/autopatch", response_model=AutopatchStatusResponse, dependencies=auth_dependencies)
    def get_autopatch() -> dict:
        return autopatch.status()

    @app.post("/api/autopatch/dial", response_model=AutopatchStatusResponse, dependencies=transmit_dependencies)
    async def autopatch_dial(body: AutopatchDialRequest, request: Request) -> dict:
        request.state.audit_detail = body.number
        error = autopatch.dial(body.number, request.state.identity.username or "local")
        if error is not None:
            raise HTTPException(status_code=409, detail=error)
        return autopatch.status()

    @app.post("/api/autopatch/hangup", response_model=AutopatchStatusResponse, dependencies=auth_dependencies)
    async def autopatch_hangup(request: Request) -> dict:
        autopatch.hangup(f"hung up from the dashboard by {request.state.identity.username or 'local'}")
        return autopatch.status()

    async def trunk_status() -> dict:
        config = service.config
        return {
            **await sip_trunk.status(),
            "in_use": config.autopatch_dial_string == TRUNK_DIAL_STRING,
            "ten_digit_prefix": config.autopatch_ten_digit_prefix,
        }

    async def change_trunk(change: Awaitable[None]) -> dict:
        try:
            await change
        except AsteriskSetupError as error:
            raise HTTPException(status_code=502, detail=str(error))
        return await trunk_status()

    def no_call_in_progress() -> None:
        if autopatch.call is not None:
            raise HTTPException(status_code=409, detail="Hang up the call first.")

    @app.get("/api/autopatch/trunk", response_model=SipTrunkStatusResponse, dependencies=auth_dependencies)
    async def get_trunk() -> dict:
        return await trunk_status()

    @app.post("/api/autopatch/trunk/modules", response_model=SipTrunkStatusResponse, dependencies=admin_dependencies)
    async def enable_trunk_modules() -> dict:
        return await change_trunk(sip_trunk.enable_modules())

    @app.put("/api/autopatch/trunk", response_model=SipTrunkStatusResponse, dependencies=admin_dependencies)
    async def put_trunk(body: SipTrunkRequest, request: Request) -> dict:
        no_call_in_progress()
        request.state.audit_detail = f"{body.username}@{body.server}" + (", new password" if body.password else "")
        trunk = TrunkSettings(
            server=body.server,
            port=body.port,
            transport=body.transport,
            username=body.username,
            auth_username=body.auth_username,
            password=body.password or None,
            registers=body.registers,
            incoming_from=tuple(body.incoming_from),
        )
        status = await change_trunk(sip_trunk.save(trunk))
        service.update_config(autopatch_dial_string=TRUNK_DIAL_STRING, autopatch_ten_digit_prefix=body.ten_digit_prefix)
        return {**status, "in_use": True, "ten_digit_prefix": body.ten_digit_prefix}

    @app.delete("/api/autopatch/trunk", response_model=SipTrunkStatusResponse, dependencies=admin_dependencies)
    async def delete_trunk() -> dict:
        no_call_in_progress()
        return await change_trunk(sip_trunk.remove())
