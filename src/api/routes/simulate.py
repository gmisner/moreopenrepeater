"""Dashboard / agent simulator endpoints (COS, CTCSS, DTMF, remote keyed)."""
from __future__ import annotations

from typing import Callable, Sequence

from fastapi import FastAPI

from ..models import (
    SimulateCOSRequest,
    SimulateCTCSSRequest,
    SimulateDTMFRequest,
    SimulateRemoteKeyedRequest,
    StatusResponse,
)
from ..service.core_handle import ApiCoreHandle


def register(
    app: FastAPI,
    *,
    service: ApiCoreHandle,
    transmit_dependencies: Sequence,
    status_response: Callable[..., StatusResponse],
) -> None:
    @app.post("/api/simulate/cos", response_model=StatusResponse, dependencies=transmit_dependencies)
    async def simulate_cos(body: SimulateCOSRequest) -> StatusResponse:
        await service.command("simulate_cos", active=body.active)
        return status_response(service.snapshot())

    @app.post("/api/simulate/ctcss", response_model=StatusResponse, dependencies=transmit_dependencies)
    async def simulate_ctcss(body: SimulateCTCSSRequest) -> StatusResponse:
        await service.command("simulate_ctcss", tone_hz=body.tone_hz)
        return status_response(service.snapshot())

    @app.post("/api/simulate/dtmf", response_model=StatusResponse, dependencies=transmit_dependencies)
    async def simulate_dtmf(body: SimulateDTMFRequest) -> StatusResponse:
        await service.command("simulate_dtmf", digit=body.digit)
        return status_response(service.snapshot())

    @app.post("/api/simulate/remote-keyed", response_model=StatusResponse, dependencies=transmit_dependencies)
    async def simulate_remote_keyed(body: SimulateRemoteKeyedRequest) -> StatusResponse:
        await service.command("simulate_remote_keyed", node_id=body.node_id, keyed=body.keyed)
        return status_response(service.snapshot())
