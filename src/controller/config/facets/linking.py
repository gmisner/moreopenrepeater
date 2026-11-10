from __future__ import annotations

from dataclasses import dataclass, field
"""LinkingConfig: config facet (flat wire field names)."""


@dataclass(frozen=True)
class LinkingConfig:
    # Nodes to connect to with one click: [{"node": str, "name": str, "monitor": bool}]
    link_favorites: list = field(default_factory=list)
    # [{node, name, days, time, minutes, monitor, enabled}]
    link_schedules: list = field(default_factory=list)  # [{node, name, days, time, minutes, monitor, enabled}]
    # DMR talkgroups (not AllStar nodes): [{"talkgroup": int, "name": str, "slot": int}]
    dmr_talkgroup_favorites: list = field(default_factory=list)
    # GMRS (47 CFR Part 95 Subpart E) instead of amateur rules: IDs at least
    # every 15 minutes, and no phone patch, linking or APRS. See controller.modes.
    gmrs_mode: bool = False
