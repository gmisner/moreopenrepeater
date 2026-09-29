"""Bridges an app_rpt node's AMI events to controller.events, and dispatches
DTMF macro commands back to app_rpt over the same connection.

Event format confirmed against app_rpt's own source
(github.com/AllStarLink/app_rpt, apps/app_rpt/rpt_link.c's
`rpt_update_links`/`__mklinklist`) and a live two-node link/unlink test
against a real AllStarLink ASL3 instance (Debian 12 arm64 Lima VM): whenever
a node's link set changes -- connect, disconnect, or a linked node's keyed
state flipping -- app_rpt re-triggers an `RPT_ALINKS` event on that node's
channel with a fresh snapshot of every adjacent link, formatted as
`<count>,<name><mode><keyed>[,<name><mode><keyed>...]` where `mode` is one of
`T`/`R`/`L`/`C` (transceive/monitor/local-monitor/connecting) and `keyed` is
`K`/`U`. A local repeater controller only cares about link presence and
keyed state, not mode, so `parse_alinks` reduces each event to
`{node_id: keyed}` and `NodeLinkClient.events` diffs successive snapshots
into `LinkStateChanged`/`RemoteKeyed` events.

The same connection connects and disconnects links (`rpt cmd <node> ilink
3|2|1|6 [<node>]`: transceive, monitor, disconnect, disconnect all) and
reads the current links on demand from `rpt xnode`'s `RPT_ALINKS=` line,
since the events can lag by seconds.
"""
from __future__ import annotations

import logging
from typing import AsyncIterator, Optional

from controller.events import ControllerEvent, LinkStateChanged, RemoteKeyed

from .ami_client import AMIClient, AMIMessage

_logger = logging.getLogger("moreopenrepeater.link")


LINK_MODES = {"T": "transceive", "R": "monitor", "L": "local monitor", "C": "connecting"}


def parse_link_modes(value: str) -> dict[str, tuple[str, bool]]:
    """Parse an RPT_ALINKS value into {node_id: (mode, keyed)}.

    Empty string or "0" both mean "no links" (app_rpt emits either
    depending on whether any adjacent link was ever present this session).
    """
    if not value or value == "0":
        return {}
    _count, _, rest = value.partition(",")
    if not rest:
        return {}
    links: dict[str, tuple[str, bool]] = {}
    for token in rest.split(","):
        if len(token) < 3:
            continue
        node_id, mode, keyed_flag = token[:-2], token[-2], token[-1]
        links[node_id] = (LINK_MODES.get(mode, mode), keyed_flag == "K")
    return links


def parse_alinks(value: str) -> dict[str, bool]:
    """Parse an RPT_ALINKS EventValue into {node_id: keyed}."""
    return {node: keyed for node, (_mode, keyed) in parse_link_modes(value).items()}


def _output(response: AMIMessage) -> list[str]:
    output = response.get("Output", [])
    return [output] if isinstance(output, str) else output


class NodeLinkClient:
    """One app_rpt local node, bridged to controller events over AMI."""

    def __init__(self, host: str, port: int, username: str, secret: str, local_node_id: str) -> None:
        self._ami = AMIClient(host, port, username, secret)
        self._local_node_id = local_node_id
        self._last_links: dict[str, bool] = {}

    async def connect(self) -> None:
        await self._ami.connect()

    async def close(self) -> None:
        await self._ami.close()

    @property
    def local_node_id(self) -> str:
        return self._local_node_id

    @local_node_id.setter
    def local_node_id(self, node_id: str) -> None:
        self._local_node_id = node_id

    async def send_macro_command(self, node_id: str, command: str) -> None:
        target = node_id or self._local_node_id
        await self._ami.send_action({"Action": "Command", "Command": f"rpt fun {target} {command}"})

    async def command(self, command: str) -> list[str]:
        """A console command's output lines."""
        return _output(await self._ami.send_action({"Action": "Command", "Command": command}))

    async def connect_node(self, node_id: str, monitor: bool = False) -> None:
        await self._ilink(2 if monitor else 3, node_id)

    async def disconnect_node(self, node_id: str) -> None:
        await self._ilink(1, node_id)

    async def disconnect_all(self) -> None:
        await self._ilink(6)

    async def links(self) -> dict[str, tuple[str, bool]]:
        """{node_id: (mode, keyed)} for every link now."""
        for line in await self.command(f"rpt xnode {self._require_node()}"):
            if line.startswith("RPT_ALINKS="):
                return parse_link_modes(line.removeprefix("RPT_ALINKS="))
        return {}

    async def echolink_callsign(self, number: str) -> Optional[str]:
        """The callsign of EchoLink node `number`, from chan_echolink's directory."""
        for line in await self.command(f"echolink dbget nodename {number}"):
            fields = line.split("|")
            if len(fields) >= 2 and fields[0] == number:
                return fields[1]
        return None

    def _require_node(self) -> str:
        if not self._local_node_id:
            raise ValueError("the repeater's AllStarLink node isn't chosen yet")
        return self._local_node_id

    async def _ilink(self, function: int, node_id: str = "") -> None:
        if node_id and not node_id.isdigit():
            raise ValueError(f"not a node number: {node_id!r}")
        await self.command(f"rpt cmd {self._require_node()} ilink {function} {node_id}".rstrip())

    async def events(self) -> AsyncIterator[ControllerEvent]:
        async for message in self._ami.events():
            for event in self._translate(message):
                yield event

    def _translate(self, message: AMIMessage) -> list[ControllerEvent]:
        if message.get("Event") != "RPT_ALINKS" or message.get("Node") != self._local_node_id:
            return []

        value = message.get("EventValue", "")
        current = parse_alinks(value if isinstance(value, str) else "")
        previous = self._last_links
        self._last_links = current

        events: list[ControllerEvent] = []
        for node_id in previous.keys() - current.keys():
            if previous[node_id]:
                events.append(RemoteKeyed(node_id=node_id, keyed=False))
            events.append(LinkStateChanged(node_id=node_id, linked=False))
        for node_id in current.keys() - previous.keys():
            events.append(LinkStateChanged(node_id=node_id, linked=True))
            if current[node_id]:
                events.append(RemoteKeyed(node_id=node_id, keyed=True))
        for node_id in current.keys() & previous.keys():
            if current[node_id] != previous[node_id]:
                events.append(RemoteKeyed(node_id=node_id, keyed=current[node_id]))
        return events
