"""DMR via DVSwitch: identity and network settings in /etc/moreopenrepeater/dmr.

Writes Analog_Bridge.ini, MMDVM_Bridge.ini, and DVSwitch.ini (ADR 0004).
The password is written into MMDVM_Bridge.ini but never returned on status.
USRP audio is api.dmr_audio; service restarts go through api.dmr_restart.
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

from link import ini_file

from .dmr_audio import DmrAudio, DmrUsrpSettings, dmr_usrp_settings_from_env
from .dmr_restart import DmrRestartControl

ANALOG = "Analog_Bridge.ini"
MMDVM = "MMDVM_Bridge.ini"
DVSWITCH = "DVSwitch.ini"
ENABLED_MARKER = "enabled"

NETWORK_DEFAULTS: dict[str, tuple[str, int]] = {
    "brandmeister": ("3102.repeater.net", 62031),
    "tgif": ("tgif.network", 62031),
    "custom": ("", 62031),
}

DEFAULT_LISTEN_PORT = 34011
DEFAULT_PEER_PORT = 34012


class DmrSetupError(Exception):
    pass


@dataclass
class DmrSettings:
    callsign: str
    dmr_id: str
    repeater_ssid: str = "01"
    password: Optional[str] = None  # None keeps the saved one
    network: Literal["brandmeister", "tgif", "custom"] = "brandmeister"
    host: str = ""
    port: int = 62031
    talkgroup: int = 9
    slot: int = 2
    color_code: int = 1
    location: str = ""
    description: str = "moreopenrepeater"
    use_emulator: bool = True


def dmr_dir_from_env(env: dict) -> Optional[Path]:
    raw = env.get("MOREOPENREPEATER_DMR_DIR")
    return Path(raw) if raw else None


def _read(path: Path) -> str:
    try:
        return path.read_text()
    except OSError as error:
        raise DmrSetupError(f"Couldn't read {path}: {error}") from error


def _write(path: Path, text: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text if text.endswith("\n") else text + "\n")
        os.replace(tmp, path)
    except OSError as error:
        raise DmrSetupError(f"Couldn't write {path}: {error}") from error


def _unit_active(name: str) -> Optional[bool]:
    import subprocess

    try:
        result = subprocess.run(
            ["systemctl", "is-active", "--quiet", name],
            check=False,
            capture_output=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode == 0:
        return True
    if result.returncode == 3:  # inactive
        return False
    return None


def _analog_template(listen_port: int, peer_port: int) -> str:
    return f"""; moreopenrepeater DMR bridge — Analog_Bridge
include = dvsm.macro

[GENERAL]
logLevel = 2
exportMetadata = true
transferRootDir = /tmp
decoderFallBack = true
useEmulator = true
emulatorAddress = 127.0.0.1:2470

[AMBE_AUDIO]
address = 127.0.0.1
txPort = 31103
rxPort = 31100
ambeMode = DMR
minTxTimeMS = 2500
gatewayDmrId = 0
repeaterID = 0
txTg = 9
txTs = 2
colorCode = 1

[USRP]
address = 127.0.0.1
txPort = {listen_port}
rxPort = {peer_port}
usrpAudio = AUDIO_UNITY
tlvAudio = AUDIO_UNITY
"""


def _mmdvm_template() -> str:
    return """; moreopenrepeater DMR bridge — MMDVM_Bridge
[General]
Callsign=N0CALL
Id=0
Timeout=180
Duplex=0

[Info]
RXFrequency=0
TXFrequency=0
Power=1
Latitude=0.0
Longitude=0.0
Height=0
Location=
Description=moreopenrepeater
URL=https://github.com/gmisner/moreopenrepeater

[Log]
DisplayLevel=1
FileLevel=2
FilePath=/var/log/mmdvm
FileRoot=MMDVM_Bridge

[Modem]
Port=/dev/null
RSSIMappingFile=/dev/null
Trace=0
Debug=0

[DMR]
Enable=1
ColorCode=1
EmbeddedLCOnly=1
DumpTAData=0

[DMR Network]
Enable=1
Address=
Port=62031
Jitter=360
Local=62032
Password=
Slot1=0
Slot2=1
Debug=0

[D-Star]
Enable=0
[System Fusion]
Enable=0
[P25]
Enable=0
[NXDN]
Enable=0
[D-Star Network]
Enable=0
[System Fusion Network]
Enable=0
[P25 Network]
Enable=0
[NXDN Network]
Enable=0
"""


def _dvswitch_template() -> str:
    return """; moreopenrepeater DMR bridge — DVSwitch.ini (MMDVM_Bridge <-> Analog_Bridge)
[DMR]
address = 127.0.0.1
txPort = 31100
rxPort = 31103
slot = 2
exportTG = 0
hangTimerInFrames = 0
"""


class DmrBridge:
    def __init__(
        self,
        directory: Optional[Path],
        audio: DmrAudio,
        restart: Optional[DmrRestartControl] = None,
        usrp: Optional[DmrUsrpSettings] = None,
    ) -> None:
        self.directory = directory
        self.audio = audio
        self.restart = restart
        self.usrp = usrp or dmr_usrp_settings_from_env(os.environ)
        self._lock = asyncio.Lock()

    def _paths(self) -> dict[str, Path]:
        assert self.directory is not None
        return {
            ANALOG: self.directory / ANALOG,
            MMDVM: self.directory / MMDVM,
            DVSWITCH: self.directory / DVSWITCH,
            ENABLED_MARKER: self.directory / ENABLED_MARKER,
        }

    def ensure_templates(self) -> None:
        """Seed default ini files once (install-pi.sh also copies these)."""
        if self.directory is None:
            return
        listen = self.usrp.listen_port if self.usrp else DEFAULT_LISTEN_PORT
        peer = self.usrp.peer_port if self.usrp else DEFAULT_PEER_PORT
        paths = self._paths()
        if not paths[ANALOG].exists():
            _write(paths[ANALOG], _analog_template(listen, peer))
        if not paths[MMDVM].exists():
            _write(paths[MMDVM], _mmdvm_template())
        if not paths[DVSWITCH].exists():
            _write(paths[DVSWITCH], _dvswitch_template())

    def _settings_from_files(self) -> Optional[dict]:
        if self.directory is None:
            return None
        paths = self._paths()
        if not paths[ANALOG].exists() or not paths[MMDVM].exists():
            return None
        analog = ini_file.section_values(_read(paths[ANALOG]), "AMBE_AUDIO")
        general = ini_file.section_values(_read(paths[ANALOG]), "GENERAL")
        mmdvm = ini_file.section_values(_read(paths[MMDVM]), "General")
        info = ini_file.section_values(_read(paths[MMDVM]), "Info")
        net = ini_file.section_values(_read(paths[MMDVM]), "DMR Network")
        dmr = ini_file.section_values(_read(paths[MMDVM]), "DMR")
        host = net.get("Address", "")
        network: str = "custom"
        for name, (default_host, _) in NETWORK_DEFAULTS.items():
            if name != "custom" and host == default_host:
                network = name
                break
        dmr_id = (analog.get("gatewayDmrId") or mmdvm.get("Id") or "").strip()
        repeater = (analog.get("repeaterID") or "").strip()
        ssid = "01"
        if dmr_id and repeater.startswith(dmr_id) and len(repeater) >= len(dmr_id) + 2:
            ssid = repeater[len(dmr_id) : len(dmr_id) + 2]
        try:
            port = int(net.get("Port") or 62031)
        except ValueError:
            port = 62031
        try:
            talkgroup = int(analog.get("txTg") or 9)
        except ValueError:
            talkgroup = 9
        try:
            slot = int(analog.get("txTs") or 2)
        except ValueError:
            slot = 2
        try:
            color_code = int(analog.get("colorCode") or dmr.get("ColorCode") or 1)
        except ValueError:
            color_code = 1
        return {
            "callsign": mmdvm.get("Callsign", ""),
            "dmr_id": dmr_id if dmr_id not in ("0", "") else "",
            "repeater_ssid": ssid,
            "has_password": bool(net.get("Password")),
            "network": network,
            "host": host,
            "port": port,
            "talkgroup": talkgroup,
            "slot": slot,
            "color_code": color_code,
            "location": info.get("Location", ""),
            "description": info.get("Description", "moreopenrepeater"),
            "use_emulator": general.get("useEmulator", "true").lower() in ("1", "true", "yes"),
        }

    async def status(self) -> dict:
        audio = self.audio.status()
        result: dict = {
            "available": False,
            "enabled": False,
            "error": None,
            "settings": None,
            "audio": audio,
            "services": {
                "analog_bridge": _unit_active("analog_bridge"),
                "mmdvm_bridge": _unit_active("mmdvm_bridge"),
                "md380_emu": _unit_active("md380-emu"),
            },
            "restart": self.restart.status() if self.restart else {"available": False, "requested": False, "last_action": None},
        }
        if self.directory is None:
            result["error"] = "DMR isn't installed. On the Pi run: sudo bash scripts/install-pi.sh --dmr"
            return result
        if not self.directory.is_dir():
            result["error"] = f"DMR config directory {self.directory} is missing. Re-run install-pi.sh --dmr."
            return result
        result["available"] = True
        try:
            self.ensure_templates()
            result["settings"] = self._settings_from_files()
            result["enabled"] = (self.directory / ENABLED_MARKER).exists()
        except DmrSetupError as error:
            result["error"] = str(error)
        return result

    async def save(self, settings: DmrSettings) -> dict:
        async with self._lock:
            if self.directory is None:
                raise DmrSetupError("DMR isn't installed. On the Pi run: sudo bash scripts/install-pi.sh --dmr")
            self.ensure_templates()
            paths = self._paths()
            current = self._settings_from_files() or {}
            password = settings.password or (None if not current.get("has_password") else _password_from_file(paths[MMDVM]))
            if not password:
                raise DmrSetupError("Enter the DMR network password.")
            if not settings.dmr_id.isdigit() or len(settings.dmr_id) != 7:
                raise DmrSetupError("DMR Id must be seven digits.")
            if not settings.repeater_ssid.isdigit() or len(settings.repeater_ssid) != 2:
                raise DmrSetupError("Repeater SSID must be two digits (01–99).")
            host = settings.host.strip()
            if not host:
                host = NETWORK_DEFAULTS.get(settings.network, ("", 62031))[0]
            if not host:
                raise DmrSetupError("Enter the DMR network host.")
            repeater_id = f"{settings.dmr_id}{settings.repeater_ssid}"
            listen = self.usrp.listen_port if self.usrp else DEFAULT_LISTEN_PORT
            peer = self.usrp.peer_port if self.usrp else DEFAULT_PEER_PORT

            analog = _read(paths[ANALOG])
            analog = ini_file.set_values(
                analog,
                "GENERAL",
                {
                    "useEmulator": "true" if settings.use_emulator else "false",
                    "decoderFallBack": "true",
                },
            )
            analog = ini_file.set_values(
                analog,
                "AMBE_AUDIO",
                {
                    "gatewayDmrId": settings.dmr_id,
                    "repeaterID": repeater_id,
                    "txTg": str(settings.talkgroup),
                    "txTs": str(settings.slot),
                    "colorCode": str(settings.color_code),
                    "ambeMode": "DMR",
                },
            )
            analog = ini_file.set_values(
                analog,
                "USRP",
                {
                    "address": "127.0.0.1",
                    "txPort": str(listen),
                    "rxPort": str(peer),
                },
            )
            _write(paths[ANALOG], analog)

            mmdvm = _read(paths[MMDVM])
            mmdvm = ini_file.set_values(
                mmdvm,
                "General",
                {"Callsign": settings.callsign.upper(), "Id": repeater_id, "Duplex": "0"},
            )
            mmdvm = ini_file.set_values(
                mmdvm,
                "Info",
                {
                    "Location": settings.location or settings.callsign.upper(),
                    "Description": settings.description or "moreopenrepeater",
                },
            )
            mmdvm = ini_file.set_values(
                mmdvm,
                "DMR",
                {"Enable": "1", "ColorCode": str(settings.color_code)},
            )
            slot1 = "1" if settings.slot == 1 else "0"
            slot2 = "1" if settings.slot == 2 else "0"
            mmdvm = ini_file.set_values(
                mmdvm,
                "DMR Network",
                {
                    "Enable": "1",
                    "Address": host,
                    "Port": str(settings.port),
                    "Password": password,
                    "Slot1": slot1,
                    "Slot2": slot2,
                },
            )
            _write(paths[MMDVM], mmdvm)

            dvswitch = _read(paths[DVSWITCH]) if paths[DVSWITCH].exists() else _dvswitch_template()
            dvswitch = ini_file.set_values(dvswitch, "DMR", {"slot": str(settings.slot)})
            _write(paths[DVSWITCH], dvswitch)

            paths[ENABLED_MARKER].write_text("1\n")
            if self.usrp is not None:
                await self.audio.configure(self.usrp)
            if self.restart is not None and self.restart.available:
                self.restart.request("restart")
        return await self.status()

    async def disable(self) -> dict:
        async with self._lock:
            if self.directory is None:
                raise DmrSetupError("DMR isn't installed.")
            marker = self.directory / ENABLED_MARKER
            if marker.exists():
                marker.unlink()
            await self.audio.configure(None)
            if self.restart is not None and self.restart.available:
                self.restart.request("stop")
        return await self.status()

    async def select_talkgroup(self, talkgroup: int, slot: Optional[int] = None) -> dict:
        """Switch the static TX talkgroup (and optional slot) without rewriting identity."""
        async with self._lock:
            if self.directory is None:
                raise DmrSetupError("DMR isn't installed.")
            if not (self.directory / ENABLED_MARKER).exists():
                raise DmrSetupError("Enable DMR first, then pick a talkgroup.")
            if talkgroup < 1:
                raise DmrSetupError("Talkgroup must be a positive integer.")
            self.ensure_templates()
            paths = self._paths()
            current = self._settings_from_files() or {}
            next_slot = slot if slot is not None else int(current.get("slot") or 2)
            if next_slot not in (1, 2):
                raise DmrSetupError("Slot must be 1 or 2.")

            analog = ini_file.set_values(
                _read(paths[ANALOG]),
                "AMBE_AUDIO",
                {"txTg": str(talkgroup), "txTs": str(next_slot)},
            )
            _write(paths[ANALOG], analog)

            mmdvm = ini_file.set_values(
                _read(paths[MMDVM]),
                "DMR Network",
                {"Slot1": "1" if next_slot == 1 else "0", "Slot2": "1" if next_slot == 2 else "0"},
            )
            _write(paths[MMDVM], mmdvm)

            dvswitch = _read(paths[DVSWITCH]) if paths[DVSWITCH].exists() else _dvswitch_template()
            _write(paths[DVSWITCH], ini_file.set_values(dvswitch, "DMR", {"slot": str(next_slot)}))

            if self.restart is not None and self.restart.available:
                self.restart.request("restart")
        return await self.status()


def _password_from_file(path: Path) -> str:
    return ini_file.section_values(_read(path), "DMR Network").get("Password", "")
