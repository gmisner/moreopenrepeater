"""The AllStar and DMR audio, for an API that runs apart from the core.

The audio is the core's: it listens for the node (or the DMR bridge) and sends to it. When the dashboard sets a node or a
bridge up, this process edits its files, and then tells the core where it is (`usrp_configure`). This is the API's side of
that: it has the same face as the in-process audio (`configure`, `stop`, `status`), so the setup code is the same in both.

What was asked for last is remembered and sent again each time the core connects, so a core that was away when the
dashboard saved (or that restarted) ends up pointed at the right place. The core also keeps it, so it does not need this.
A refusal is kept to show on the page: the files were saved, and the page says what the core did not do.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from .service.core_handle import CommandRefused, CoreError

_logger = logging.getLogger("moreopenrepeater.remote_usrp")


def _peer(settings: Any) -> Optional[dict]:
    """Where to send: the node (AllStar) or the bridge (DMR), whichever settings these are."""
    if settings is None:
        return None
    host = getattr(settings, "node_host", None) or getattr(settings, "peer_host", None)
    port = getattr(settings, "node_port", None) or getattr(settings, "peer_port", None)
    return {"host": host, "port": port}


class RemoteUsrpAudio:
    def __init__(self, remote, service: str, part: str) -> None:
        """`service` is `allstar` or `dmr` (the command's name for it); `part` is the core hardware part whose status
        has this audio's (`allstar_node` or `dmr`)."""
        self._remote = remote
        self._service = service
        self._part = part
        self.settings: Any = None  # what was last asked for
        self._asked = False
        self._error: Optional[str] = None
        self._lock = asyncio.Lock()
        on_connect = getattr(remote, "on_connect", None)
        if on_connect is not None:
            on_connect(self._core_connected)

    def status(self) -> dict:
        try:
            status = dict(self._remote.hardware_status(self._part).get("audio") or {})
        except CoreError:
            status = {"configured": self.settings is not None, "running": False, "error": None, "keyed": False, "node": None}
        if self._error and not status.get("error"):
            status["error"] = self._error
        return status

    async def start(self) -> None:  # the core starts it
        return None

    async def stop(self) -> None:  # and stops it
        return None

    async def configure(self, settings: Any) -> None:
        """Remember `settings` (None for no node) and tell the core. Never raises: the files are saved either way,
        and the core is told again when it is reachable."""
        self.settings = settings
        self._asked = True
        await self._send()

    def _core_connected(self) -> None:
        if self._asked:
            asyncio.get_running_loop().create_task(self._send())

    async def _send(self) -> None:
        async with self._lock:
            settings = self.settings
            try:
                await self._remote.command("usrp_configure", service=self._service, peer=_peer(settings))
            except CommandRefused as refused:
                self._error = str(refused)
                _logger.warning("the core did not point the %s audio there: %s", self._service, refused.code)
            except CoreError as error:
                _logger.info("the core was not told where the %s is yet (%s); it is told when it connects", self._service, type(error).__name__)
            else:
                self._error = None
