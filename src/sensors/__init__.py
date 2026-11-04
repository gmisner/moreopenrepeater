"""Site sensor hardware adapters (1-wire and I2C ADC).

Missing devices return ``None`` so the poll loop can skip gracefully.
Tests inject fake readers instead of touching ``/sys`` or ``/dev/i2c-*``.
"""
from __future__ import annotations

from .ads1115 import read_ads1115_volts
from .ds18b20 import read_ds18b20_celsius
from .speech import speak_reading, speak_value

__all__ = [
    "read_ads1115_volts",
    "read_ds18b20_celsius",
    "speak_reading",
    "speak_value",
]
