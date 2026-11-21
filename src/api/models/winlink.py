"""Winlink gateway status model (read-only)."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel


class WinlinkCheck(BaseModel):
    id: Literal["gateway_admin", "gateway_telnet", "modem_kiss"]
    ok: bool
    reason: str


class WinlinkSession(BaseModel):
    id: str
    callsign: Optional[str]  # the connecting station, shown to the signed-in user only: never in the log
    started_at: str  # UTC, ISO 8601
    duration_s: int
    messages_in: int  # messages the station sent up to Winlink through this gateway
    messages_out: int  # messages Winlink delivered to the station
    bytes_in: int  # message sizes before compression, not bytes on the air
    bytes_out: int
    outcome: Literal["completed", "ended_early", "unknown"]


class WinlinkGatewayCounters(BaseModel):
    sessions_today: int  # on the station's own calendar day
    sessions_today_is_minimum: bool  # the reporter's list was full, so there may have been more
    sessions_total: int
    incomplete: int
    messages_in: int
    messages_out: int
    bytes_in: int
    bytes_out: int
    connects: int  # connections accepted on the radio
    failed_connects: int  # of those, the ones that never reached Winlink
    last_session_at: Optional[str]
    since: Optional[str]  # the counts start again when the gateway container restarts


class WinlinkGateway(BaseModel):
    counters: WinlinkGatewayCounters
    sessions: list[WinlinkSession]  # newest first


class WinlinkModemCounters(BaseModel):
    frames_heard: int
    frames_sent: int  # Dire Wolf does not log each time it keys the transmitter, so this is the nearest thing it reports
    last_heard_at: Optional[str]
    last_sent_at: Optional[str]
    since: Optional[str]


class WinlinkModem(BaseModel):
    counters: WinlinkModemCounters


class WinlinkActivity(BaseModel):
    gateway: Optional[WinlinkGateway]
    modem: Optional[WinlinkModem]
    notes: list[str]  # plain words for whatever is missing


class WinlinkStatusResponse(BaseModel):
    enabled: bool
    callsign: Optional[str]
    checks: list[WinlinkCheck]
    problems: list[str]
    activity: Optional[WinlinkActivity] = None  # None: this install has no status folder
