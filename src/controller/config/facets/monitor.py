from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
"""MonitorConfig: config facet (flat wire field names)."""


@dataclass(frozen=True)
class MonitorConfig:
    # A listen-only second receiver (api.monitor_receiver); never transmitted.
    monitor_enabled: bool = False
    # "Aviation 119.1"
    monitor_name: str = ''  # "Aviation 119.1"
    monitor_input_device: str = ''
    monitor_squelch: Literal['vox', 'gpio', 'open'] = 'vox'
    monitor_vox_threshold_db: float = -40.0
    # header pin 15
    monitor_gpio_pin: int = 22  # header pin 15
    monitor_gpio_polarity: Literal['low', 'high'] = 'low'
    monitor_gain_db: float = 0.0
    monitor_record: bool = False
