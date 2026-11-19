"""The AllStar link service (#221, #229): app_rpt's view of the links, carried to the repeater and back.

It holds the AMI connection to the app_rpt node (`link.node_link.NodeLinkClient`), and its only way to the
repeater is the small interface the API already has to the core: `link_event` and `link_sync` in, and a
macro's link command out (the core sends `link` actions to whoever handles them, and
`LinkBridge.command` is that handler). Because it needs nothing else from the core, it runs the same whether
the core is in this process or in its own container, and can move to a process of its own without
changing.

It never holds up the radio. A connection to app_rpt that cannot be made, or is lost, is a log line and a
retry in a few seconds, and in the meantime the core is told there are no links, so a node that was
transmitting when the connection dropped cannot leave the repeater thinking it still is.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Optional

from controller.events import ControllerEvent, LinkStateChanged
from link.node_link import NodeLinkClient
from observability import log_event

_logger = logging.getLogger("moreopenrepeater.link")

RECONNECT_DELAY_SECONDS = 5.0
SNAPSHOT_TIMEOUT_SECONDS = 5.0


class LinkBridge:
    def __init__(
        self,
        service,
        settings,
        *,
        node: Callable[[], str] = lambda: "",
        client_factory: Optional[Callable[..., NodeLinkClient]] = None,
        on_client: Callable[[Optional[NodeLinkClient]], None] = lambda client: None,
        on_linked: Callable[[str], Awaitable[None]] = None,  # type: ignore[assignment]
        reconnect_delay: Optional[float] = None,
    ) -> None:
        """`service` is what link events go to (`link_event`, `link_sync`, `held_reason`); `settings` is the
        AMI host, port, username, secret and the node it was told to use; `node()` is the node to use when
        the settings do not say. `on_client` hears the client while connected (and None when not), so the
        dashboard's link controls can use the same connection. `on_linked(node)` is called when a node
        links to us from outside while linking is held, so it can be dropped."""
        self._service = service
        self._settings = settings
        self._node = node
        self._factory = client_factory  # None: the real client, looked up when it is needed
        self._on_client = on_client
        self._on_linked = on_linked
        self._delay = RECONNECT_DELAY_SECONDS if reconnect_delay is None else reconnect_delay
        self.client: Optional[NodeLinkClient] = None

    def command(self, node_id: str, command: str) -> Optional[Awaitable[None]]:
        """A macro's link command (connect a node, a function), from the core. A coroutine for the caller to
        run, or None, with a log line, when there is no connection to app_rpt to send it on."""
        client = self.client
        if client is None:
            log_event(
                _logger, logging.WARNING, "link.command_dropped", "a link command was dropped: not connected to app_rpt",
                error_code="link.not_connected", facts_in_text=False,
            )
            return None
        return client.send_macro_command(node_id, command)

    async def _snapshot(self, client: NodeLinkClient) -> dict[str, bool]:
        """The links as app_rpt has them now, for the core's picture. Empty if it cannot say yet (the node is
        not chosen, or the answer did not come): the events that follow fill it in."""
        try:
            links = await asyncio.wait_for(client.links(), SNAPSHOT_TIMEOUT_SECONDS)
        except (ValueError, OSError, ConnectionError, asyncio.TimeoutError):
            return {}
        return {node: keyed for node, (_mode, keyed) in links.items()}

    async def run(self) -> None:
        settings = self._settings
        while True:
            node_id = settings.local_node_id or self._node()
            client = (self._factory or NodeLinkClient)(
                settings.host, settings.port, settings.username, settings.secret, node_id
            )
            try:
                await client.connect()
                log_event(
                    _logger, logging.INFO, "link.connected",
                    f"connected to app_rpt AMI at {settings.host}:{settings.port} (node {node_id or 'not chosen yet'})",
                    host=settings.host, port=settings.port, node=node_id or None, facts_in_text=False,
                )
                self.client = client
                self._on_client(client)
                picture = await self._snapshot(client)
                client.remember_links(picture)  # so the events that follow are changes, not the same picture again
                self._service.link_sync(picture)
                async for event in client.events():
                    self._service.link_event(event)
                    await self._maybe_drop(event)
            except (OSError, ConnectionError):
                log_event(
                    _logger, logging.ERROR, "link.connection_lost",
                    f"app_rpt AMI connection lost; reconnecting in {self._delay}s",
                    error_code="link.ami_connection", retry_s=self._delay, exc_info=True, facts_in_text=False,
                )
            finally:
                self.client = None
                self._on_client(None)
                self._service.link_sync({})  # app_rpt is out of sight: no links, and none keyed
                await client.close()
            await asyncio.sleep(self._delay)

    async def _maybe_drop(self, event: ControllerEvent) -> None:
        if (
            self._on_linked is not None and isinstance(event, LinkStateChanged) and event.linked
            and self._service.held_reason("links")
        ):
            asyncio.create_task(self._on_linked(event.node_id))  # a node linked to us from outside
