"""EchoLink through the repeater's AllStarLink node (app_rpt's chan_echolink).

EchoLink stations become links of the node, so what works for AllStar
links -- their audio, key-ups, the link list, `*3` to connect -- works for
them too. EchoLink node numbers are padded to six digits and prefixed with
a 3: `*33009999` connects to EchoLink node 9999.

The station's details go in echolink.conf's [el0] section, marked the same
way as the node's lines in rpt.conf (link.asl_config). The password is
written there but never read back out to the dashboard.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Optional

from link.asl_config import MARK, control_section, module_state, restore_module, section_settings, set_module

from .allstar_node import AllStarNode, AllStarSetupError

SECTION = "el0"
MODULE = "chan_echolink.so"
_UNSET = ("", "INVALID", "000000", "YOUR NAME")  # ASL3's placeholders


@dataclass
class EchoLinkSettings:
    callsign: str
    name: str
    location: str
    email: str
    node_number: str
    password: Optional[str] = None  # None keeps the saved one
    lat: float = 0.0
    lon: float = 0.0
    frequency_mhz: float = 0.0
    tone_hz: float = 0.0
    max_stations: int = 20


def _value(settings: dict[str, str], key: str) -> str:
    value = settings.get(key, "")
    return "" if value in _UNSET else value


def _number(settings: dict[str, str], key: str) -> float:
    try:
        return float(settings.get(key, "0") or 0)
    except ValueError:
        return 0.0


class EchoLink:
    def __init__(self, node: AllStarNode) -> None:
        self._node = node
        self._lock = asyncio.Lock()

    async def status(self) -> dict:
        result: dict = {"available": False, "enabled": False, "loaded": None, "error": None, "settings": None}
        if not self._node.can_edit:
            return result
        try:
            text = await self._node.read_file("echolink.conf")
            modules = await self._node.read_file("modules.conf")
            current = section_settings(text, SECTION)
        except (AllStarSetupError, ValueError) as error:
            result["error"] = f"EchoLink isn't set up in this AllStarLink install: {error}"
            return result
        result["available"] = True
        result["enabled"] = module_state(modules, MODULE) not in (None, "noload")
        result["settings"] = {
            "callsign": _value(current, "call"),
            "name": _value(current, "name"),
            "location": _value(current, "qth"),
            "email": _value(current, "email"),
            "node_number": _value(current, "node"),
            "has_password": bool(_value(current, "pwd")),
            "lat": _number(current, "lat"),
            "lon": _number(current, "lon"),
            "frequency_mhz": _number(current, "freq"),
            "tone_hz": _number(current, "tone"),
            "max_stations": int(_number(current, "maxstns") or 20),
            "astnode": current.get("astnode", ""),
            "set_here": MARK in text,
        }
        if result["enabled"]:
            result["loaded"] = await self._node.module_loaded(MODULE)
        return result

    async def save(self, settings: EchoLinkSettings) -> dict:
        async with self._lock:
            if self._node.node is None:
                raise AllStarSetupError("Choose the repeater's AllStarLink node first.")
            text = await self._node.read_file("echolink.conf")
            modules = await self._node.read_file("modules.conf")
            try:
                current = section_settings(text, SECTION)
            except ValueError:
                raise AllStarSetupError(f"echolink.conf has no [{SECTION}] section.")
            password = settings.password or _value(current, "pwd")
            if not password:
                raise AllStarSetupError("Enter the EchoLink password.")
            text = control_section(text, SECTION, {
                "call": settings.callsign,
                "pwd": password,
                "name": settings.name,
                "qth": settings.location,
                "email": settings.email,
                "node": settings.node_number,
                "lat": f"{settings.lat:.5f}",
                "lon": f"{settings.lon:.5f}",
                "freq": f"{settings.frequency_mhz:.4f}",
                "tone": f"{settings.tone_hz:.1f}",
                "maxstns": str(settings.max_stations),
                "astnode": self._node.node,
            })
            await self._node.write_file("echolink.conf", text)
            await self._node.write_file("modules.conf", set_module(modules, MODULE, True))
            await self._node.restart_asterisk()
        return await self.status()

    async def disable(self) -> dict:
        """Stop loading chan_echolink. The station's details stay for next time."""
        async with self._lock:
            modules = restore_module(await self._node.read_file("modules.conf"), MODULE)
            if module_state(modules, MODULE) not in (None, "noload"):
                modules = set_module(modules, MODULE, False)
            await self._node.write_file("modules.conf", modules)
            await self._node.restart_asterisk()
        return await self.status()
