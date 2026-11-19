"""Link node bookkeeping and remote-keyed / link-radio events."""
from __future__ import annotations

import logging
import time
from collections import deque
from typing import TYPE_CHECKING

from controller.events import ControllerEvent, LinkStateChanged, RemoteKeyed

from .snapshot import LINK_AUDIO_NODE, LINK_DMR_NODE, LINK_RADIO_NODE

if TYPE_CHECKING:
    from .facade import RepeaterService

_logger = logging.getLogger("moreopenrepeater.service")

LINK_EVENT_LIMIT = 100
# USRP/RF peers whose own key-up is authoritative while AllStar audio is up.
_AUDIO_KEY_NODES = frozenset({LINK_AUDIO_NODE, LINK_DMR_NODE, LINK_RADIO_NODE})


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
        if isinstance(event, RemoteKeyed) and self.audio and event.node_id not in _AUDIO_KEY_NODES:
            # The node's own key-up (with its audio) says when to transmit;
            # a linked station can be keyed without the node sending it.
            # Parallel bridges (DMR, Linked Radio) keep their own key path.
            return
        if isinstance(event, LinkStateChanged):
            if event.linked:
                self.nodes.add(event.node_id)
            else:
                if event.node_id in host.controller.remote_keyed_nodes:
                    # A node that is no longer linked cannot be transmitting to us, whether or not it said so.
                    self.handle_event(RemoteKeyed(node_id=event.node_id, keyed=False))
                self.nodes.discard(event.node_id)
            self._record("connected" if event.linked else "disconnected", event.node_id)
        elif isinstance(event, RemoteKeyed):
            self._record("keyed" if event.keyed else "unkeyed", event.node_id)
        host._apply_commands(host.controller.handle_event(event, host._clock()))

    def sync(self, nodes: dict[str, bool]) -> None:
        """Make the picture of the links match `nodes` ({node: keyed}), the whole current state as the link
        service sees it. A node we hold that is missing is gone, and a node that was keyed is unkeyed first (a
        disconnect does that): when the link service loses app_rpt it sends an empty picture, so a node that was
        transmitting when the connection dropped cannot leave the repeater thinking it still is. The nodes that carry
        the node's own audio or another bridge (USRP, DMR, the linked radio) are not the link service's to
        speak for and are left alone."""
        host = self._host
        mine = {node for node in self.nodes if node not in _AUDIO_KEY_NODES}
        ours_keyed = set(host.controller.remote_keyed_nodes)
        for node in sorted(mine - nodes.keys()):
            self.handle_event(LinkStateChanged(node_id=node, linked=False))  # which unkeys it first, if it was keyed
        for node in sorted(nodes.keys() - mine):
            self.handle_event(LinkStateChanged(node_id=node, linked=True))
        for node, keyed in sorted(nodes.items()):
            if keyed != (node in ours_keyed and node in mine):
                self.handle_event(RemoteKeyed(node_id=node, keyed=keyed))

    def radio_keyed(self, keyed: bool) -> None:
        """The link radio started or stopped hearing the far end."""
        _logger.info("link radio %s", "receiving" if keyed else "clear")
        self._record("keyed" if keyed else "unkeyed", LINK_RADIO_NODE)
        host = self._host
        host._apply_commands(
            host.controller.handle_event(RemoteKeyed(node_id=LINK_RADIO_NODE, keyed=keyed), host._clock())
        )
