from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional
"""RemoteBaseConfig: config facet (flat wire field names)."""


@dataclass(frozen=True)
class RemoteBaseConfig:
    # "" = no CAT control; tune the radio by hand
    remote_base_rigctld: str = 'localhost:4532'  # "" = no CAT control; tune the radio by hand
    remote_base_rig_mode: Literal['FM', 'AM', 'USB', 'LSB'] = 'FM'
    remote_base_mhz: float = 146.52
    remote_base_shift: Literal['simplex', 'plus', 'minus'] = 'simplex'
    # None: the band's usual offset
    remote_base_offset_mhz: Optional[float] = None  # None: the band's usual offset
    # transmit CTCSS
    remote_base_tone_hz: Optional[float] = None  # transmit CTCSS
    # MHz it may transmit on
    remote_base_ranges: str = '144-148, 222-225, 420-450'  # MHz it may transmit on
    # turns itself off after this long unused; 0 = stays on
    remote_base_idle_minutes: float = 10.0  # turns itself off after this long unused; 0 = stays on

# Optional fields the dashboard may null out via clear_<name> on ConfigUpdateRequest.
CLEAR_FIELDS = ('remote_base_offset_mhz', 'remote_base_tone_hz')

