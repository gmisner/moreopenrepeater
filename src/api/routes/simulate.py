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
from ..service import RepeaterService


def register(
    app: FastAPI,
    *,
    service: RepeaterService,
    transmit_dependencies: Sequence,
    status_response: Callable[..., StatusResponse],
) -> None:
    @app.post("/api/simulate/cos", response_model=StatusResponse, dependencies=transmit_dependencies)
    async def simulate_cos(body: SimulateCOSRequest) -> StatusResponse:
        service.simulate_cos(body.active)
        return status_response(service.snapshot())

    @app.post("/api/simulate/ctcss", response_model=StatusResponse, dependencies=transmit_dependencies)
    async def simulate_ctcss(body: SimulateCTCSSRequest) -> StatusResponse:
        service.simulate_ctcss(body.tone_hz)
        return status_response(service.snapshot())

    @app.post("/api/simulate/dtmf", response_model=StatusResponse, dependencies=transmit_dependencies)
    async def simulate_dtmf(body: SimulateDTMFRequest) -> StatusResponse:
        service.simulate_dtmf(body.digit)
        return status_response(service.snapshot())

    @app.post("/api/simulate/remote-keyed", response_model=StatusResponse, dependencies=transmit_dependencies)
    async def simulate_remote_keyed(body: SimulateRemoteKeyedRequest) -> StatusResponse:
        service.simulate_remote_keyed(body.node_id, body.keyed)
        return status_response(service.snapshot())
