"""PortAudio device listing and stable identity across USB re-enumeration.

ALSA card numbers in PortAudio names (`… (hw:3,0)`) move when a USB sound
card resets. Levels are already keyed by control name; this module keeps a
side map of PortAudio name → USB identity so engines can reopen the same
physical dongle after `hw:N` changes.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Callable, Optional

from audio_io.audio_stream import sd

from .boards import alsa_card

_logger = logging.getLogger("moreopenrepeater.audio_devices")

_HW_SUFFIX = re.compile(r"\s*\(hw:\d+,\d+\)\s*$")
_CACHE_NAME = "audio-device-ids.json"

# In-process map updated whenever devices are listed; optionally persisted.
_name_to_id: dict[str, str] = {}
_cache_path: Optional[Path] = None


def device_stem(name: str) -> str:
    """PortAudio name without the trailing `(hw:N,M)` card address."""
    return _HW_SUFFIX.sub("", name or "").strip()


def set_cache_path(path: Optional[Path]) -> None:
    """Where to remember name → USB id across restarts (`None` = memory only)."""
    global _cache_path, _name_to_id
    _cache_path = path
    _name_to_id = _load_cache(path) if path is not None else {}


def _load_cache(path: Path) -> dict[str, str]:
    try:
        data = json.loads(path.read_text())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(key): str(value) for key, value in data.items() if value}


def _save_cache() -> None:
    if _cache_path is None:
        return
    try:
        _cache_path.parent.mkdir(parents=True, exist_ok=True)
        _cache_path.write_text(json.dumps(_name_to_id, indent=2, sort_keys=True) + "\n")
    except OSError as error:
        _logger.debug("couldn't save audio device id cache: %s", error)


def _read_text(path: Path) -> Optional[str]:
    try:
        return path.read_text().strip()
    except (OSError, UnicodeDecodeError):
        return None


def alsa_card_stable_id(card: int, sys_root: Path = Path("/")) -> Optional[str]:
    """Identity for ALSA card N that survives USB re-enumeration when possible.

    Prefers `usb-vendor:product-serial`, then bus path, then `/proc` usbid.
    """
    card_sys = sys_root / "sys/class/sound" / f"card{card}"
    try:
        device = (card_sys / "device").resolve()
    except OSError:
        device = None
    if device is not None:
        for path in (device, *device.parents):
            vendor = _read_text(path / "idVendor")
            product = _read_text(path / "idProduct")
            if not vendor or not product:
                continue
            serial = (_read_text(path / "serial") or "").strip()
            if serial and serial not in ("0", "n/a", "N/A"):
                return f"usb-{vendor}:{product}-{serial}"
            busnum = _read_text(path / "busnum")
            devpath = _read_text(path / "devpath")
            if busnum and devpath:
                return f"usb-{vendor}:{product}-{busnum}-{devpath}"
            return f"usb-{vendor}:{product}-{path.name}"
    usbid = _read_text(sys_root / "proc/asound" / f"card{card}" / "usbid")
    if usbid:
        return f"usbid-{usbid}"
    return None


def remember_devices(devices: list[dict]) -> None:
    """Record name → stable_id for later resolve after hw:N moves."""
    changed = False
    for device in devices:
        name = device.get("name")
        stable_id = device.get("stable_id")
        if not name or not stable_id:
            continue
        for key in (name, device_stem(name)):
            if _name_to_id.get(key) != stable_id:
                _name_to_id[key] = stable_id
                changed = True
    if changed:
        _save_cache()


def list_audio_devices(
    query: Optional[Callable[[], list]] = None,
    card_id: Callable[[int], Optional[str]] = alsa_card_stable_id,
) -> list[dict]:
    """PortAudio devices with ALSA card number and stable USB id when known."""
    if query is not None:
        raw = query()
    else:
        if sd is None:
            return []
        raw = sd.query_devices()
    devices = []
    for entry in raw:
        name = entry["name"]
        card = alsa_card(name)
        devices.append(
            {
                "name": name,
                "inputs": entry["max_input_channels"] if "max_input_channels" in entry else entry.get("inputs", 0),
                "outputs": entry["max_output_channels"] if "max_output_channels" in entry else entry.get("outputs", 0),
                "default_samplerate": entry.get("default_samplerate", 0),
                "card": card,
                "stable_id": card_id(card) if card is not None else None,
            }
        )
    remember_devices(devices)
    return devices


def resolve_audio_device(
    wanted: Optional[str],
    devices: Optional[list[dict]] = None,
    *,
    list_devices: Callable[[], list[dict]] = list_audio_devices,
) -> Optional[str]:
    """Current PortAudio name for a saved device, even if `hw:N` moved.

    Exact match first; then the remembered USB id; then a unique stem match.
    Returns `wanted` unchanged when nothing better is found (caller still gets
    a clear PortAudio error).
    """
    if not wanted:
        return wanted
    available = devices if devices is not None else list_devices()
    names = {device["name"] for device in available}
    if wanted in names:
        return wanted
    stable_id = _name_to_id.get(wanted) or _name_to_id.get(device_stem(wanted))
    if stable_id:
        hits = [device for device in available if device.get("stable_id") == stable_id]
        if len(hits) == 1:
            resolved = hits[0]["name"]
            if resolved != wanted:
                _logger.info("resolved audio device %r → %r (stable id %s)", wanted, resolved, stable_id)
            return resolved
    stem = device_stem(wanted)
    hits = [device for device in available if device_stem(device["name"]) == stem]
    if len(hits) == 1:
        resolved = hits[0]["name"]
        if resolved != wanted:
            _logger.info("resolved audio device %r → %r (unique name stem)", wanted, resolved)
        return resolved
    return wanted
