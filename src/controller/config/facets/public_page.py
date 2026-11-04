from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
"""PublicPageConfig: config facet (flat wire field names)."""


@dataclass(frozen=True)
class PublicPageConfig:
    # The listening page at /listen (api.app): off, for signed-in accounts
    # only (including listener accounts), or for anyone.
    public_page_mode: Literal['off', 'signed_in', 'anyone'] = 'off'
    # e.g. the frequency and tone
    public_page_text: str = ''  # e.g. the frequency and tone
    # let visitors listen live
    public_page_audio: bool = True  # let visitors listen live
    public_page_max_listeners: int = 20
