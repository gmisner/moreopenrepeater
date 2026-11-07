"""Link node bookkeeping and remote-keyed / link-radio events."""
from __future__ import annotations

import logging
import time
from collections import deque
from typing import TYPE_CHECKING

from controller.events import ControllerEvent, LinkStateChanged, RemoteKeyed

from .snapshot import LINK_AUDIO_NODE, LINK_RADIO_NODE

if TYPE_CHECKING:
    from .facade import RepeaterService

_logger = logging.getLogger("moreopenrepeater.service")

LINK_EVENT_LIMIT = 100


class LinkSupport:
    """Tracks linked nodes and feeds link events into the controller."""

    def __init__(self, host: "RepeaterService") -> None:
        self._host = host
        self.nodes: set[str] = set()
        self.audio = False  # api.allstar_audio is carrying the node's audio
        self._events: deque[dict] = deque(maxlen=LINK_EVENT_LIMIT)

    def recent_events(self, limit: int = 50) -> list[dict]:
        """Newest-first link activity for the dashboard."""
        return list(self._events)[: max(0, limit)]

    def _record(self, kind: str, node_id: str) -> None:
        # USRP keyed spam is not useful on the dashboard; keep linked-radio RX.
        if node_id == LINK_AUDIO_NODE:
            return
        self._events.appendleft({"at": time.time(), "kind": kind, "node": node_id})

    def simulate_remote_keyed(self, node_id: str, keyed: bool) -> None:
        _logger.info("simulate_remote_keyed(node_id=%r, keyed=%s)", node_id, keyed)
        if keyed:
            self.nodes.add(node_id)
        else:
            self.nodes.discard(node_id)
        self._record("keyed" if keyed else "unkeyed", node_id)
        host = self._host
        host._apply_commands(host.controller.handle_event(RemoteKeyed(node_id=node_id, keyed=keyed), host._clock()))

    def handle_event(self, event: ControllerEvent) -> None:
        """Apply a real event from `link.node_link.NodeLinkClient` -- the
        same shape as simulate_remote_keyed's bookkeeping, but driven by the
        network layer instead of a dashboard button."""
        _logger.info("handle_link_event(%r)", event)
        host = self._host
        if isinstance(event, RemoteKeyed) and self.audio and event.node_id != LINK_AUDIO_NODE:
            # The node's own key-up (with its audio) says when to transmit;
            # a linked station can be keyed without the node sending it.
            return
        if isinstance(event, LinkStateChanged):
            if event.linked:
                self.nodes.add(event.node_id)
            else:
                self.nodes.discard(event.node_id)
            self._record("connected" if event.linked else "disconnected", event.node_id)
        elif isinstance(event, RemoteKeyed):
            self._record("keyed" if event.keyed else "unkeyed", event.node_id)
        host._apply_commands(host.controller.handle_event(event, host._clock()))

    def radio_keyed(self, keyed: bool) -> None:
        """The link radio started or stopped hearing the far end."""
        _logger.info("link radio %s", "receiving" if keyed else "clear")
        self._record("keyed" if keyed else "unkeyed", LINK_RADIO_NODE)
        host = self._host
        host._apply_commands(
            host.controller.handle_event(RemoteKeyed(node_id=LINK_RADIO_NODE, keyed=keyed), host._clock())
        )
