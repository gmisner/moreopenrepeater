"""Clock check and NTP server setting (docs/docker.md, The time container)."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class TimeContainerStatus(BaseModel):
    """What the optional time (chrony) container says about itself. `running` is false when it is not installed or has gone quiet."""

    running: bool
    synced: Optional[bool]
    stratum: Optional[int]
    offset_ms: Optional[float]
    server: Optional[str]
    config_error: Optional[str]
    updated_at: Optional[float]


class TimeStatusResponse(BaseModel):
    server: str  # the host that is checked: the setting, or the default while it is empty
    configured_server: str  # what was set; empty means the default
    default_server: str
    state: Literal["ok", "off", "unreachable", "not_checked"]
    reachable: Optional[bool]  # None: not checked
    offset_ms: Optional[float]  # the server's time minus the Pi's clock: positive means the Pi is behind
    round_trip_ms: Optional[float]
    stratum: Optional[int]
    limit_ms: int  # off by this much or more is "off"
    checked_at: Optional[float]  # Unix time of the check (a check is reused for about a minute)
    error: Optional[str]  # why there was no reading: resolve, timeout, network, kiss, unsynchronized, bad_reply
    error_text: Optional[str]
    container: TimeContainerStatus
    install: Literal["native", "container"]  # a native install has no time container, so the page leaves that card out


class TimeServerRequest(BaseModel):
    server: str = Field(default="", max_length=253)  # a host name or an IP address; empty for the default
