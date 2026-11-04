"""Shared config literals used by identity / tone facets."""
from __future__ import annotations

from typing import Literal

# Built-in courtesy tones (playout.tones); "same" uses the local one.
CourtesyStyle = Literal[
    "beep", "high_low", "low_high", "triple", "chirp", "bumblebee", "up_run", "down_run", "bonk", "bee_boo",
    "nextel", "cw_k", "cw_r", "cw_t", "custom",
]
CourtesyVariant = Literal["same", CourtesyStyle]
