from __future__ import annotations

from dataclasses import dataclass
"""HomeassistantConfig: config facet (flat wire field names)."""


@dataclass(frozen=True)
class HomeassistantConfig:
    # e.g. http://homeassistant.local:8123, for `homeassistant` macros
    homeassistant_url: str = ''  # e.g. http://homeassistant.local:8123, for `homeassistant` macros
    # "Done." / "Failed." after a Home Assistant macro
    homeassistant_say_result: bool = True  # "Done." / "Failed." after a Home Assistant macro
