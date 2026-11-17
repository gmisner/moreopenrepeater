"""Status the core publishes, and the single site-location source."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal, Optional

from pydantic import Field, model_validator

from .common import Freshness, Message
from .events import PttEvidence


class CoreStatus(Message):
    """A snapshot of the repeater, with commanded and observed PTT kept apart.

    `ptt_commanded` is what the core asked for. It is not proof of
    transmission. `ptt_observed` and `ptt_evidence` say what was seen, and
    `ptt_observed: null` means "could not tell".
    """

    kind: Literal["core.status"] = "core.status"
    #: The controller's own state name (`idle`, `receiving`, `hang_time`, ...).
    state: str = Field(pattern=r"^[a-z][a-z_]*$", max_length=40)
    transmitter_enabled: bool
    locked_out: bool
    cos_active: bool
    ptt_commanded: bool
    ptt_observed: Optional[bool] = None
    ptt_evidence: PttEvidence = PttEvidence.NONE
    tot_remaining_s: Optional[float] = Field(default=None, ge=0)
    hang_remaining_s: Optional[float] = Field(default=None, ge=0)
    id_due_in_s: Optional[float] = Field(default=None, ge=0)
    # Added in contract 1.1 (optional, so a 1.0 reader is unaffected): what the
    # dashboard shows beside the state.
    ctcss_hz: Optional[float] = None
    linked_nodes: list[str] = Field(default_factory=list, max_length=32)
    net_active: bool = False
    gmrs_mode: bool = False
    callsign: str = Field(default="", max_length=16)


class LocationSource(str, Enum):
    FIXED = "fixed"  # typed in by the owner
    GPS = "gps"  # from the GPS module


class FixQuality(str, Enum):
    NONE = "none"
    FIX_2D = "2d"
    FIX_3D = "3d"


class SiteLocation(Message):
    """Where the repeater is. One source of truth: weather, APRS, the map and
    other services read this and never open the GPS device (ADR 0005 item 8).

    The coordinates are real. The log filter coarsens them, and a support
    bundle must do the same.
    """

    kind: Literal["site.location"] = "site.location"
    source: LocationSource
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    altitude_m: Optional[float] = None
    #: GPS only.
    fix: Optional[FixQuality] = None
    #: GPS only. A fixed location never goes stale, so it has none.
    freshness: Optional[Freshness] = None

    @model_validator(mode="after")
    def _gps_needs_fix_and_freshness(self) -> "SiteLocation":
        if self.source is LocationSource.GPS and (self.fix is None or self.freshness is None):
            raise ValueError("a GPS location needs a fix quality and freshness")
        if self.source is LocationSource.FIXED and (self.fix is not None or self.freshness is not None):
            raise ValueError("a fixed location has no fix quality or freshness")
        return self

    def usable(self, now: datetime) -> bool:
        """False for a GPS location with no fix or one that has gone stale, so
        consumers fall back or stop instead of acting on an old position."""
        if self.source is LocationSource.FIXED:
            return True
        return self.fix is not FixQuality.NONE and not self.freshness.is_stale(now)
