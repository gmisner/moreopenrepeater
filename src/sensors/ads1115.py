"""ADS1115 16-bit ADC over Linux ``/dev/i2c-*`` (no pip deps).

Single-shot reads of AIN0–AIN3 vs GND at ±4.096 V full-scale. Missing bus
or device returns ``None``. Apply scale/offset in the site-sensor service
for voltage dividers and such.
"""
from __future__ import annotations

import fcntl
import os
import struct
import time
from typing import Optional

I2C_SLAVE = 0x0703
REG_CONVERSION = 0x00
REG_CONFIG = 0x01
# PGA ±4.096 V → 125 µV/LSB; MUX AIN0–3 vs GND; single-shot; 128 SPS.
_PGA_4_096 = 0b001
_MODE_SINGLE = 0b1
_DR_128 = 0b100
_OS_START = 0b1
_COMP_QUE_DISABLE = 0b11
FULL_SCALE_V = 4.096
LSB_V = FULL_SCALE_V / 32768.0


def _config_word(channel: int) -> int:
    mux = 0b100 | (channel & 0b11)  # AINx vs GND
    return (
        (_OS_START << 15)
        | (mux << 12)
        | (_PGA_4_096 << 9)
        | (_MODE_SINGLE << 8)
        | (_DR_128 << 5)
        | _COMP_QUE_DISABLE
    )


def read_ads1115_volts(
    channel: int = 0,
    *,
    bus: int = 1,
    address: int = 0x48,
    i2c_path: Optional[str] = None,
) -> Optional[float]:
    """Return the channel voltage, or ``None`` if the bus/device is absent."""
    if channel < 0 or channel > 3:
        return None
    path = i2c_path or f"/dev/i2c-{bus}"
    try:
        fd = os.open(path, os.O_RDWR)
    except OSError:
        return None
    try:
        try:
            fcntl.ioctl(fd, I2C_SLAVE, address)
        except OSError:
            return None
        config = _config_word(channel)
        try:
            os.write(fd, bytes([REG_CONFIG, (config >> 8) & 0xFF, config & 0xFF]))
        except OSError:
            return None
        # 128 SPS ≈ 8 ms; wait a bit longer for the conversion bit.
        time.sleep(0.012)
        try:
            os.write(fd, bytes([REG_CONVERSION]))
            raw = os.read(fd, 2)
        except OSError:
            return None
        if len(raw) != 2:
            return None
        (counts,) = struct.unpack(">h", raw)
        return counts * LSB_V
    finally:
        os.close(fd)
