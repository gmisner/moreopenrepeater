"""Winlink gateway status model (read-only)."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel


class WinlinkCheck(BaseModel):
    id: Literal["gateway_admin", "gateway_telnet", "modem_kiss"]
    ok: bool
    reason: str


class WinlinkStatusResponse(BaseModel):
    enabled: bool
    callsign: Optional[str]
    checks: list[WinlinkCheck]
    problems: list[str]
