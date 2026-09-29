"""The repeater's AllStarLink links from the dashboard: which nodes are
linked (with callsigns), connecting and disconnecting them, and favorites.

EchoLink stations appear as node numbers of seven digits starting with 3
(3 + the EchoLink number padded to six digits); their callsigns come from
chan_echolink's directory. Everything else is looked up in AllStarLink's
node list (`api.node_directory`).
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Callable, Optional

from link.node_link import NodeLinkClient

from .node_directory import NodeDirectory

if TYPE_CHECKING:
    from .service import RepeaterService

_logger = logging.getLogger("moreopenrepeater.link")

AMI_TIMEOUT = 5.0
ECHOLINK_CACHE_SECONDS = 3600
ECHOLINK_MISS_SECONDS = 300  # chan_echolink may not have loaded the directory yet


class LinkError(Exception):
    pass


def echolink_number(node: str) -> Optional[str]:
    if len(node) == 7 and node.startswith("3") and node.isdigit():
        return str(int(node[1:]))
    return None


class LinkControl:
    def __init__(self, service: RepeaterService, directory: NodeDirectory, local_node: Callable[[], str] = lambda: "") -> None:
        """`local_node()` is the repeater's node number, which can change while connected."""
        self._service = service
        self._directory = directory
        self._local_node = local_node
        self._client: Optional[NodeLinkClient] = None
        self._echolink: dict[str, tuple[Optional[str], float]] = {}

    @property
    def client(self) -> Optional[NodeLinkClient]:
        if self._client is not None:
            self._client.local_node_id = self._local_node()
        return self._client

    @client.setter
    def client(self, client: Optional[NodeLinkClient]) -> None:
        self._client = client

    async def status(self) -> dict:
        client = self.client
        error = None
        if client is None:
            links = {node: (None, False) for node in self._service.linked_nodes}
        else:
            try:
                links = await asyncio.wait_for(client.links(), AMI_TIMEOUT)
            except (OSError, ConnectionError, asyncio.TimeoutError, ValueError) as failure:
                error = f"couldn't read the links from app_rpt: {failure}"
                links = {}
        names = {favorite["node"]: favorite.get("name", "") for favorite in self._service.config.link_favorites}
        rows = [
            {**await self.describe(node), "mode": mode, "keyed": keyed, "name": names.get(node, "")}
            for node, (mode, keyed) in sorted(links.items())
        ]
        favorites = [
            {**await self.describe(favorite["node"]), "name": favorite.get("name", ""), "monitor": bool(favorite.get("monitor"))}
            for favorite in self._service.config.link_favorites
        ]
        return {
            "available": client is not None and bool(client.local_node_id),
            "node": client.local_node_id if client else None,
            "error": error,
            "links": rows,
            "favorites": favorites,
        }

    async def describe(self, node: str) -> dict:
        number = echolink_number(node)
        if number is not None:
            return {"node": node, "kind": "echolink", "callsign": await self._echolink_callsign(number) or "", "description": f"EchoLink {number}", "location": ""}
        info = self._directory.lookup(node) or {}
        return {
            "node": node,
            "kind": "allstar",
            "callsign": info.get("callsign", ""),
            "description": info.get("description", ""),
            "location": info.get("location", ""),
        }

    async def linked(self) -> dict[str, tuple[str, bool]]:
        client = self._require_client()
        try:
            return await asyncio.wait_for(client.links(), AMI_TIMEOUT)
        except (OSError, ConnectionError, asyncio.TimeoutError, ValueError) as error:
            raise LinkError(f"couldn't read the links from app_rpt: {error}") from error

    async def connect(self, node: str, monitor: bool) -> None:
        client = self._require_client()
        await self._run(client.connect_node(node, monitor))
        _logger.info("connecting node %s (%s)", node, "monitor" if monitor else "transceive")

    async def disconnect(self, node: str) -> None:
        client = self._require_client()
        await self._run(client.disconnect_node(node))
        _logger.info("disconnecting node %s", node)

    async def disconnect_all(self) -> None:
        client = self._require_client()
        await self._run(client.disconnect_all())
        _logger.info("disconnecting all links")

    def _require_client(self) -> NodeLinkClient:
        if self.client is None:
            raise LinkError("The repeater isn't connected to AllStarLink (MOREOPENREPEATER_AMI_HOST).")
        if not self.client.local_node_id:
            raise LinkError("Choose the repeater's AllStarLink node first.")
        return self.client

    async def _run(self, action) -> None:
        try:
            await asyncio.wait_for(action, AMI_TIMEOUT)
        except (OSError, ConnectionError, asyncio.TimeoutError, ValueError) as error:
            raise LinkError(f"app_rpt didn't take the command: {error}") from error

    async def _echolink_callsign(self, number: str) -> Optional[str]:
        cached = self._echolink.get(number)
        if cached is not None and time.monotonic() - cached[1] < (ECHOLINK_CACHE_SECONDS if cached[0] else ECHOLINK_MISS_SECONDS):
            return cached[0]
        client = self.client
        if client is None:
            return None
        try:
            callsign = await asyncio.wait_for(client.echolink_callsign(number), AMI_TIMEOUT)
        except (OSError, ConnectionError, asyncio.TimeoutError):
            return None
        self._echolink[number] = (callsign, time.monotonic())
        return callsign
