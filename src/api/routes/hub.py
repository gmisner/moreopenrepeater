"""Multi-repeater hub: configure remote sites and poll their status."""
from __future__ import annotations

import asyncio
from typing import Sequence

from fastapi import FastAPI, HTTPException, Request

from ..hub import HubError, HubPoller, HubSite, HubSiteSnapshot, HubSiteStore
from ..models import (
    HubSiteCreateRequest,
    HubSiteResponse,
    HubSiteStatusResponse,
    HubSiteUpdateRequest,
)


def _site_response(site: HubSite) -> HubSiteResponse:
    return HubSiteResponse(
        id=site.id,
        name=site.name,
        base_url=site.base_url,
        dashboard_url=site.dashboard_url,
        token_set=bool(site.api_token),
    )


def _status_response(snapshot: HubSiteSnapshot) -> HubSiteStatusResponse:
    return HubSiteStatusResponse(**snapshot.__dict__)


def register(
    app: FastAPI,
    *,
    hub_sites: HubSiteStore,
    hub_poller: HubPoller,
    admin_dependencies: Sequence,
) -> None:
    @app.get("/api/hub/sites", response_model=list[HubSiteResponse], dependencies=admin_dependencies)
    def list_sites() -> list[HubSiteResponse]:
        return [_site_response(site) for site in hub_sites.list()]

    @app.post("/api/hub/sites", response_model=list[HubSiteResponse], dependencies=admin_dependencies)
    def create_site(body: HubSiteCreateRequest, request: Request) -> list[HubSiteResponse]:
        request.state.audit_detail = f"{body.name} @ {body.base_url}"
        try:
            hub_sites.add(body.name, body.base_url, body.api_token)
        except HubError as error:
            raise HTTPException(status_code=400, detail=str(error)) from None
        return list_sites()

    @app.put("/api/hub/sites/{site_id}", response_model=list[HubSiteResponse], dependencies=admin_dependencies)
    def update_site(site_id: str, body: HubSiteUpdateRequest, request: Request) -> list[HubSiteResponse]:
        request.state.audit_detail = site_id
        try:
            hub_sites.update(site_id, name=body.name, base_url=body.base_url, api_token=body.api_token)
        except KeyError:
            raise HTTPException(status_code=404, detail="No such site") from None
        except HubError as error:
            raise HTTPException(status_code=400, detail=str(error)) from None
        return list_sites()

    @app.delete("/api/hub/sites/{site_id}", response_model=list[HubSiteResponse], dependencies=admin_dependencies)
    def delete_site(site_id: str, request: Request) -> list[HubSiteResponse]:
        try:
            site = hub_sites.remove(site_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="No such site") from None
        request.state.audit_detail = f"{site.name} @ {site.base_url}"
        return list_sites()

    @app.get("/api/hub/sites/status", response_model=list[HubSiteStatusResponse], dependencies=admin_dependencies)
    async def poll_sites() -> list[HubSiteStatusResponse]:
        sites = hub_sites.list()
        snapshots = await asyncio.to_thread(hub_poller.poll_all, sites)
        return [_status_response(snapshot) for snapshot in snapshots]

    @app.get(
        "/api/hub/sites/{site_id}/status",
        response_model=HubSiteStatusResponse,
        dependencies=admin_dependencies,
    )
    async def poll_site(site_id: str) -> HubSiteStatusResponse:
        try:
            site = hub_sites.get(site_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="No such site") from None
        snapshot = await asyncio.to_thread(hub_poller.poll, site)
        return _status_response(snapshot)
