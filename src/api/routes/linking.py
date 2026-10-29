"""AllStar, EchoLink, node links, GPIO outputs, and link-radio / monitor."""
from __future__ import annotations

import asyncio
from typing import Awaitable, Sequence

from fastapi import FastAPI, HTTPException, Request
from fastapi import Path as UrlPath

from ..allstar_node import AllStarNode, AllStarSetupError
from ..autopatch import Autopatch
from ..echolink import EchoLink, EchoLinkSettings
from ..gpio import GpioControl, GpioError
from ..link_radio import LinkRadio
from ..link_schedule import LinkScheduler
from ..links import LinkControl, LinkError
from ..models import (
    AllStarNodeRequest,
    AllStarStatusResponse,
    EchoLinkRequest,
    EchoLinkStatusResponse,
    GpioOutputRequest,
    GpioStatusResponse,
    LinkConnectRequest,
    LinkRadioStatus,
    LinksResponse,
    MonitorReceiverStatus,
)
from ..monitor_receiver import MonitorReceiverService
from ..remote_base import RemoteBase
from ..service import RepeaterService
from ..tx_fan import TxFan


def register(
    app: FastAPI,
    *,
    service: RepeaterService,
    autopatch: Autopatch,
    allstar_node: AllStarNode,
    links: LinkControl,
    link_scheduler: LinkScheduler,
    gpio: GpioControl,
    tx_fan: TxFan,
    monitor_receiver: MonitorReceiverService,
    link_radio: LinkRadio,
    remote_base: RemoteBase,
    auth_dependencies: Sequence,
    admin_dependencies: Sequence,
    transmit_dependencies: Sequence,
) -> None:
    def no_call_in_progress() -> None:
        if autopatch.call is not None:
            raise HTTPException(status_code=409, detail="Hang up the call first.")

    async def change_allstar(change: Awaitable[dict]) -> dict:
        try:
            return await change
        except AllStarSetupError as error:
            raise HTTPException(status_code=502, detail=str(error))

    @app.get("/api/allstar", response_model=AllStarStatusResponse, dependencies=auth_dependencies)
    async def get_allstar() -> dict:
        return await allstar_node.status()

    @app.put("/api/allstar", response_model=AllStarStatusResponse, dependencies=admin_dependencies)
    async def put_allstar(body: AllStarNodeRequest, request: Request) -> dict:
        no_call_in_progress()
        request.state.audit_detail = f"node {body.node}"
        return await change_allstar(allstar_node.use(body.node))

    @app.delete("/api/allstar", response_model=AllStarStatusResponse, dependencies=admin_dependencies)
    async def delete_allstar() -> dict:
        no_call_in_progress()
        return await change_allstar(allstar_node.release())

    echolink = EchoLink(allstar_node)

    @app.get("/api/allstar/echolink", response_model=EchoLinkStatusResponse, dependencies=auth_dependencies)
    async def get_echolink() -> dict:
        return await echolink.status()

    @app.put("/api/allstar/echolink", response_model=EchoLinkStatusResponse, dependencies=admin_dependencies)
    async def put_echolink(body: EchoLinkRequest, request: Request) -> dict:
        no_call_in_progress()
        request.state.audit_detail = f"{body.callsign}, node {body.node_number}" + (", new password" if body.password else "")
        settings = EchoLinkSettings(**{**body.model_dump(), "password": body.password or None})
        return await change_allstar(echolink.save(settings))

    @app.delete("/api/allstar/echolink", response_model=EchoLinkStatusResponse, dependencies=admin_dependencies)
    async def delete_echolink() -> dict:
        no_call_in_progress()
        return await change_allstar(echolink.disable())

    async def links_status() -> dict:
        status = await links.status()
        for row in status["links"]:
            row["until"] = link_scheduler.until(row["node"])
        status["schedules"] = link_scheduler.status(service.wall_now())
        return status

    async def change_links(action) -> dict:
        try:
            await action
        except LinkError as error:
            raise HTTPException(status_code=409, detail=str(error))
        return await links_status()

    @app.get("/api/links", response_model=LinksResponse, dependencies=auth_dependencies)
    async def get_links() -> dict:
        return await links_status()

    @app.post("/api/links", response_model=LinksResponse, dependencies=transmit_dependencies)
    async def post_link(body: LinkConnectRequest, request: Request) -> dict:
        request.state.audit_detail = f"node {body.node}" + (" (monitor)" if body.monitor else "")
        return await change_links(links.connect(body.node, body.monitor))

    @app.delete("/api/links", response_model=LinksResponse, dependencies=auth_dependencies)
    async def delete_links() -> dict:
        return await change_links(links.disconnect_all())

    @app.delete("/api/links/{node}", response_model=LinksResponse, dependencies=transmit_dependencies)
    async def delete_link(node: str = UrlPath(pattern=r"^[0-9]{1,10}$")) -> dict:
        return await change_links(links.disconnect(node))

    def gpio_status() -> dict:
        return {**gpio.status(), "fan": tx_fan.status()}

    @app.get("/api/gpio", response_model=GpioStatusResponse, dependencies=auth_dependencies)
    def get_gpio() -> dict:
        return gpio_status()

    @app.put("/api/gpio/{pin}", response_model=GpioStatusResponse, dependencies=auth_dependencies)
    def put_gpio(pin: int, body: GpioOutputRequest, request: Request) -> dict:
        request.state.audit_detail = f"GPIO{pin} ({gpio.name(pin)}) {'on' if body.on else 'off'}"
        try:
            gpio.set_output(pin, body.on)
        except GpioError as error:
            raise HTTPException(status_code=409, detail=str(error))
        return gpio_status()

    @app.get("/api/monitor-receiver", response_model=MonitorReceiverStatus, dependencies=auth_dependencies)
    def get_monitor_receiver() -> MonitorReceiverStatus:
        return MonitorReceiverStatus(**monitor_receiver.status())

    @app.get("/api/link-radio", response_model=LinkRadioStatus, dependencies=auth_dependencies)
    def get_link_radio() -> LinkRadioStatus:
        return LinkRadioStatus(**link_radio.status(), **remote_base.status())

    @app.post("/api/link-radio/tune", response_model=LinkRadioStatus, dependencies=auth_dependencies)
    async def retune_remote_base() -> LinkRadioStatus:
        remote_base.retune()
        await asyncio.sleep(0)
        return LinkRadioStatus(**link_radio.status(), **remote_base.status())
