"""Net control: start/end, check-ins, CSV export, history delete."""
from __future__ import annotations

from datetime import datetime
from typing import Callable, Sequence

from fastapi import FastAPI, HTTPException, Request, Response

from ..models import NetCheckInRequest, NetStartRequest, NetStatusResponse
from ..net import NetError, NetMode


def register(
    app: FastAPI,
    *,
    nets: NetMode,
    actor_of: Callable[[Request], str],
    auth_dependencies: Sequence,
    transmit_dependencies: Sequence,
) -> None:
    @app.get("/api/net", response_model=NetStatusResponse, dependencies=auth_dependencies)
    def get_net() -> dict:
        return nets.status()

    @app.post("/api/net/start", response_model=NetStatusResponse, dependencies=transmit_dependencies)
    async def start_net(body: NetStartRequest, request: Request) -> dict:
        try:
            net = await nets.start(actor_of(request), body.name)
        except NetError as error:
            raise HTTPException(status_code=409, detail=str(error)) from None
        request.state.audit_detail = net["name"]
        return nets.status()

    @app.post("/api/net/end", response_model=NetStatusResponse, dependencies=transmit_dependencies)
    async def end_net(request: Request) -> dict:
        try:
            net = await nets.end(actor_of(request))
        except NetError as error:
            raise HTTPException(status_code=409, detail=str(error)) from None
        request.state.audit_detail = f"{net['name']}: {len(net['checkins'])} check-ins"
        return nets.status()

    @app.post("/api/net/checkins", response_model=NetStatusResponse, dependencies=auth_dependencies)
    def add_checkin(body: NetCheckInRequest, request: Request) -> dict:
        request.state.audit_detail = body.callsign.upper()
        try:
            nets.add_checkin(body.callsign, body.notes)
        except NetError as error:
            raise HTTPException(status_code=409, detail=str(error)) from None
        return nets.status()

    @app.put("/api/net/checkins/{checkin_id}", response_model=NetStatusResponse, dependencies=auth_dependencies)
    def update_checkin(checkin_id: str, body: NetCheckInRequest) -> dict:
        try:
            nets.update_checkin(checkin_id, body.callsign, body.notes)
        except NetError as error:
            raise HTTPException(status_code=409, detail=str(error)) from None
        except KeyError:
            raise HTTPException(status_code=404, detail="No such check-in") from None
        return nets.status()

    @app.delete("/api/net/checkins/{checkin_id}", response_model=NetStatusResponse, dependencies=auth_dependencies)
    def delete_checkin(checkin_id: str) -> dict:
        try:
            nets.delete_checkin(checkin_id)
        except NetError as error:
            raise HTTPException(status_code=409, detail=str(error)) from None
        except KeyError:
            raise HTTPException(status_code=404, detail="No such check-in") from None
        return nets.status()

    @app.get("/api/nets/{net_id}/checkins.csv", dependencies=auth_dependencies)
    def net_csv(net_id: str) -> Response:
        try:
            net = nets.find(net_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="No such net") from None
        day = datetime.fromtimestamp(net["started_at"]).strftime("%Y-%m-%d")
        slug = "".join(c if c.isalnum() else "-" for c in net["name"].lower()).strip("-") or "net"
        return Response(
            nets.csv(net_id),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{slug}-{day}.csv"'},
        )

    @app.delete("/api/nets/{net_id}", response_model=NetStatusResponse, dependencies=auth_dependencies)
    def delete_net(net_id: str) -> dict:
        try:
            nets.delete(net_id)
        except NetError as error:
            raise HTTPException(status_code=409, detail=str(error)) from None
        except KeyError:
            raise HTTPException(status_code=404, detail="No such net") from None
        return nets.status()
