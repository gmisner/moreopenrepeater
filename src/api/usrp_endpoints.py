"""Where the core's AllStar and DMR audio goes, as the dashboard last set it.

With the AllStar node and the DMR bridge in their own containers, the dashboard's setup pages are served by the API, which
edits their files. The audio, though, is the core's: it listens for the node and sends to it. When the API sets a node up
it tells the core where the node is (`usrp_configure`), and the core keeps that here, in a small file next to its other
state, so a restart of the core (with the API away, or restarting too) still has the audio the repeater was set up with.

What the environment says (MOREOPENREPEATER_USRP_NODE, MOREOPENREPEATER_DMR_USRP_PEER) is the owner's own setting and
wins over a saved one for the AllStar node. Nothing here is a secret: a host and a port.
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Optional

from contracts.commands import USRP_SERVICES

_logger = logging.getLogger("moreopenrepeater.usrp_endpoints")

_HOST = re.compile(r"^[A-Za-z0-9_.:-]{1,253}$")
Peer = tuple[str, int]


class UsrpEndpoints:
    def __init__(self, path: Optional[Path]) -> None:
        self._path = path

    def load(self) -> dict[str, Peer]:
        """The saved endpoints, each one checked again: a file that was edited or damaged gives nothing, not a crash."""
        if self._path is None:
            return {}
        try:
            raw = json.loads(self._path.read_text())
        except (OSError, ValueError):
            return {}
        found: dict[str, Peer] = {}
        if not isinstance(raw, dict):
            return found
        for service in USRP_SERVICES:
            entry = raw.get(service)
            if not isinstance(entry, dict):
                continue
            host, port = entry.get("host"), entry.get("port")
            if (
                isinstance(host, str) and _HOST.match(host)
                and isinstance(port, int) and not isinstance(port, bool) and 1 <= port <= 65535
            ):
                found[service] = (host, port)
        return found

    def save(self, service: str, peer: Optional[Peer]) -> None:
        """Keep `peer` for `service` (None forgets it). A failure to write is logged, not raised: the audio is set up
        either way, and the API sets it again when it next starts."""
        if self._path is None:
            return
        current = self.load()
        if peer is None:
            current.pop(service, None)
        else:
            current[service] = peer
        body = {name: {"host": host, "port": port} for name, (host, port) in current.items()}
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_name(self._path.name + ".tmp")
            tmp.write_text(json.dumps(body))
            os.replace(tmp, self._path)
        except OSError as error:
            _logger.warning("couldn't keep where the %s audio goes: %s", service, error.strerror or type(error).__name__)
