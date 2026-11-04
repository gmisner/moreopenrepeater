"""DS18B20 temperature via the Linux 1-wire sysfs driver.

Enable with ``dtoverlay=w1-gpio`` (and usually ``w1-therm``). Each sensor
shows up under ``/sys/bus/w1/devices/28-*/w1_slave``. Missing hardware or
a CRC failure returns ``None``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

DEFAULT_W1_ROOT = Path("/sys/bus/w1/devices")


def _parse_w1_slave(text: str) -> Optional[float]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 2 or not lines[0].endswith("YES"):
        return None
    marker = "t="
    if marker not in lines[1]:
        return None
    try:
        milli = int(lines[1].rsplit(marker, 1)[1])
    except ValueError:
        return None
    return milli / 1000.0


def list_ds18b20_ids(w1_root: Path = DEFAULT_W1_ROOT) -> list[str]:
    try:
        return sorted(path.name for path in w1_root.glob("28-*") if path.is_dir())
    except OSError:
        return []


def read_ds18b20_celsius(
    device_id: str = "",
    *,
    w1_root: Path = DEFAULT_W1_ROOT,
) -> Optional[float]:
    """Read one DS18B20. Blank ``device_id`` uses the first ``28-*`` found."""
    ids = [device_id] if device_id.strip() else list_ds18b20_ids(w1_root)
    if not ids:
        return None
    path = w1_root / ids[0] / "w1_slave"
    try:
        return _parse_w1_slave(path.read_text())
    except (OSError, UnicodeDecodeError):
        return None
