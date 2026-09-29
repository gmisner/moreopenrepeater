"""Making an AllStarLink node's radio the controller, from the dashboard.

Writes the settings docs/allstar.md describes into the node's stanza in
rpt.conf (see link.asl_config for why not over AMI), loads chan_usrp, and
turns off chan_simpleusb and chan_usbradio when no node uses them any more
-- otherwise they'd open the same USB sound card the controller uses. An
rxchannel change needs an Asterisk restart (a reload of app_rpt keeps the
old channel), which goes over AMI. Then api.allstar_audio is pointed at the
node.

The node the controller is the radio for is whichever has its settings in
rpt.conf, so there's nothing to keep in step: at startup it's read back.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Callable, Optional

from link.ami_client import AMIClient
from link.asl_config import (
    control_node,
    controller_settings,
    module_state,
    nodes,
    release_node,
    restore_module,
    set_module,
)
from link.asterisk_files import AsteriskFiles, AsteriskFilesError

from .allstar_audio import AllStarAudio, UsrpSettings
from .autopatch import AMI_TIMEOUT, PatchSettings

_logger = logging.getLogger("moreopenrepeater.allstar_node")

FIRST_NODE_PORT = 32001
# Radio channel drivers that open a USB sound card, by rxchannel prefix.
USB_DRIVERS = {"simpleusb": "chan_simpleusb.so", "usbradio": "chan_usbradio.so"}


class AllStarSetupError(Exception):
    pass


class AllStarNode:
    def __init__(
        self,
        files: Optional[AsteriskFiles],
        ami: Optional[PatchSettings],
        audio: AllStarAudio,
        listen: tuple[str, int],
        address: Optional[str] = None,
        manual: Optional[UsrpSettings] = None,
        ami_factory: Callable[..., AMIClient] = AMIClient,
    ) -> None:
        """`listen` is where the controller takes the node's audio, `address`
        the host Asterisk reaches it at (default: the listen host, or this
        machine when that's every interface). `manual` is a node set up by
        hand in the environment, which the dashboard then leaves alone."""
        self._files = files
        self._ami = ami
        self.audio = audio
        self._listen = listen
        self._address = address or (listen[0] if listen[0] not in ("0.0.0.0", "::") else "127.0.0.1")
        self._manual = manual
        self._ami_factory = ami_factory
        self._lock = asyncio.Lock()
        self.restart_needed = False

    @property
    def _node_host(self) -> str:
        return self._ami.ami_host if self._ami else "127.0.0.1"

    async def start(self) -> None:
        if self._manual is not None:
            await self.audio.configure(self._manual)
            return
        if self._files is None:
            return
        try:
            text = await self._read("rpt.conf")
        except AllStarSetupError as error:
            _logger.warning("%s", error)
            return
        await self.audio.configure(self._settings_for(text))

    async def stop(self) -> None:
        await self.audio.stop()

    async def status(self) -> dict:
        result: dict = {
            "available": self._files is not None and self._manual is None,
            "manual": self._manual is not None,
            "can_restart": self._ami is not None,
            "restart_needed": self.restart_needed,
            "error": None,
            "nodes": [],
            "node": None,
            "audio": self.audio.status(),
        }
        if not result["available"]:
            return result
        try:
            text = await self._read("rpt.conf")
        except AllStarSetupError as error:
            result["error"] = str(error)
            return result
        found = nodes(text)
        result["nodes"] = [
            {"number": n.number, "rxchannel": n.rxchannel, "duplex": n.duplex, "controlled": n.controlled} for n in found
        ]
        result["node"] = next((n.number for n in found if n.controlled), None)
        return result

    async def use(self, number: str) -> dict:
        async with self._lock:
            self._require_files()
            rpt = await self._read("rpt.conf")
            modules = await self._read("modules.conf")
            found = {n.number: n for n in nodes(rpt)}
            if number not in found:
                raise AllStarSetupError(f"rpt.conf has no node {number}.")
            for other in found.values():
                if other.controlled and other.number != number:
                    rpt = release_node(rpt, other.number)
            node_port = self._node_port(rpt, number)
            rxchannel = f"USRP/{self._address}:{self._listen[1]}:{node_port}"
            rpt = control_node(rpt, number, controller_settings(rxchannel))
            modules = self._usb_drivers(rpt, set_module(modules, "chan_usrp.so", True))
            await self._write("modules.conf", modules)
            await self._write("rpt.conf", rpt)
            _logger.info("node %s: radio is now the controller (%s)", number, rxchannel)
            await self._restart()
            await self.audio.configure(self._settings_for(rpt))
        return await self.status()

    async def release(self) -> dict:
        async with self._lock:
            self._require_files()
            rpt = await self._read("rpt.conf")
            modules = await self._read("modules.conf")
            controlled = [n.number for n in nodes(rpt) if n.controlled]
            for number in controlled:
                rpt = release_node(rpt, number)
            for module in ("chan_usrp.so", *USB_DRIVERS.values()):
                modules = restore_module(modules, module)
            await self._write("modules.conf", modules)
            await self._write("rpt.conf", rpt)
            if controlled:
                _logger.info("node %s: radio settings put back", ", ".join(controlled))
                await self._restart()
            await self.audio.configure(None)
        return await self.status()

    # -- helpers -------------------------------------------------------------

    def _settings_for(self, rpt: str) -> Optional[UsrpSettings]:
        for node in nodes(rpt):
            usrp = node.usrp()
            if node.controlled and usrp is not None:
                return UsrpSettings(self._listen[0], self._listen[1], self._node_host, usrp[2])
        return None

    def _node_port(self, rpt: str, number: str) -> int:
        taken = {usrp[2] for n in nodes(rpt) if n.number != number and (usrp := n.usrp())}
        port = FIRST_NODE_PORT
        while port in taken:
            port += 1
        return port

    def _usb_drivers(self, rpt: str, modules: str) -> str:
        """Keep a USB radio driver from grabbing the controller's sound card."""
        channels = [n.rxchannel.lower() for n in nodes(rpt)]
        for prefix, module in USB_DRIVERS.items():
            in_use = any(channel.startswith(prefix + "/") for channel in channels)
            if not in_use and module_state(modules, module) not in (None, "noload"):
                modules = set_module(modules, module, False)
        return modules

    def _require_files(self) -> None:
        if self._manual is not None:
            raise AllStarSetupError("The node is set up by MOREOPENREPEATER_USRP_NODE; change it there.")
        if self._files is None:
            raise AllStarSetupError("AllStarLink's rpt.conf wasn't found on this machine.")

    async def _read(self, name: str) -> str:
        assert self._files is not None
        try:
            return await asyncio.get_running_loop().run_in_executor(None, self._files.read, name)
        except AsteriskFilesError as error:
            raise AllStarSetupError(str(error))

    async def _write(self, name: str, text: str) -> None:
        assert self._files is not None
        try:
            await asyncio.get_running_loop().run_in_executor(None, self._files.write, name, text)
        except AsteriskFilesError as error:
            raise AllStarSetupError(str(error))

    async def _restart(self) -> None:
        """`core restart now`: the node's channel only changes on a restart.
        Asterisk closes AMI without answering once it's going down."""
        if self._ami is None:
            self.restart_needed = True
            return
        s = self._ami
        ami = self._ami_factory(s.ami_host, s.ami_port, s.ami_username, s.ami_secret)
        try:
            try:
                await asyncio.wait_for(ami.connect(), AMI_TIMEOUT)
            except (ConnectionError, OSError, asyncio.TimeoutError) as error:
                self.restart_needed = True
                raise AllStarSetupError(f"Saved, but couldn't reach Asterisk to restart it ({error or 'timed out'}). Restart Asterisk to apply it.")
            response = await asyncio.wait_for(ami.send_action({"Action": "Command", "Command": "core restart now"}), AMI_TIMEOUT)
            if response.get("Response") not in ("Success", "Follows"):
                self.restart_needed = True
                raise AllStarSetupError(
                    f"Saved, but Asterisk refused to restart ({response.get('Message')}). "
                    "The AMI login needs command permission; or restart Asterisk yourself."
                )
        except (ConnectionError, OSError):
            pass  # it closed the connection: restarting
        except asyncio.TimeoutError:
            self.restart_needed = True
            raise AllStarSetupError("Saved, but Asterisk didn't answer. Restart Asterisk to apply it.")
        finally:
            try:
                await ami.close()
            except OSError:
                pass
        self.restart_needed = False
