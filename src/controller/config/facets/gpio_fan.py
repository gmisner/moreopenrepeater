from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional
"""GpioFanConfig: config facet (flat wire field names)."""


@dataclass(frozen=True)
class GpioFanConfig:
    # Spare CM108 GPIO pins by number ("1", "2", "4"-"8"): {"mode": "output" | "input", "name": str}.
    # Inputs can also have "invert" and "on_say"/"off_say"/"on_macro"/"off_macro".
    gpio_pins: dict = field(default_factory=dict)
    # [{pin, days, time, minutes, enabled}], outputs on for a weekly window
    gpio_schedules: list = field(default_factory=list)  # [{pin, days, time, minutes, enabled}], outputs on for a weekly window
    # A cooling fan that runs while transmitting and for a while after (api.tx_fan).
    fan_output: Literal['none', 'cm108', 'gpio'] = 'none'
    # a spare pin not set up on the GPIO card
    fan_cm108_pin: int = 1  # a spare pin not set up on the GPIO card
    # header pin 37
    fan_gpio_pin: int = 26  # header pin 37
    # the level that runs the fan
    fan_polarity: Literal['high', 'low'] = 'high'  # the level that runs the fan
    fan_run_on_minutes: float = 3.0
    # also run while the CPU is at least this hot
    fan_temp_c: Optional[float] = None  # also run while the CPU is at least this hot

# Optional fields the dashboard may null out via clear_<name> on ConfigUpdateRequest.
CLEAR_FIELDS = ('fan_temp_c',)

