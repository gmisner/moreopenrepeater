from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional
from controller.config.types import CourtesyVariant
"""NetConfig: config facet (flat wire field names)."""


@dataclass(frozen=True)
class NetConfig:
    # Net mode: these apply while a net is running (see controller.modes).
    net_name: str = 'Net'
    net_tot_duration: float = 600.0
    net_hang_time: float = 1.0
    net_courtesy_tone_style: CourtesyVariant = 'same'
    net_courtesy_tone_asset_id: Optional[str] = None
    net_hold_autopatch: bool = True
    # scheduled ones play after the net; weather alerts never wait
    net_hold_announcements: bool = True  # scheduled ones play after the net; weather alerts never wait
    net_links: Literal['leave', 'disconnect', 'connect'] = 'leave'
    # with net_links "connect": linked for the net, dropped after
    net_link_node: str = ''  # with net_links "connect": linked for the net, dropped after
    # said when the net starts / ends; "" = nothing
    net_start_say: str = ''  # said when the net starts / ends; "" = nothing
    net_end_say: str = ''
    # a net left running ends by itself
    net_max_minutes: float = 120.0  # a net left running ends by itself
    # [{days, time, minutes, enabled}]
    net_schedules: list = field(default_factory=list)  # [{days, time, minutes, enabled}]

# Optional fields the dashboard may null out via clear_<name> on ConfigUpdateRequest.
CLEAR_FIELDS = ('net_courtesy_tone_asset_id',)

