"""Autopatch and SIP trunk API models."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

from .types import _SOURCE

class AutopatchDialRequest(BaseModel):
    number: str = Field(min_length=1, max_length=15, pattern=r"^[0-9]+$")


class AutopatchCallResponse(BaseModel):
    number: str
    actor: str
    state: Literal["dialing", "connected", "ended"]
    started_at: float
    connected_at: Optional[float]
    ended_at: Optional[float]
    result: str
    direction: Literal["outgoing", "incoming"]


class AutopatchStatusResponse(BaseModel):
    available: bool  # Asterisk is configured and the AudioSocket server is listening
    configured: bool
    error: Optional[str]
    enabled: bool
    call: Optional[AutopatchCallResponse]
    last_call: Optional[AutopatchCallResponse]


class SipTrunkRequest(BaseModel):
    # These are written into pjsip.conf and SIP URIs.
    server: str = Field(max_length=253, pattern=r"^[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?$")
    port: int = Field(default=5060, ge=1, le=65535)
    transport: Literal["udp", "tcp"] = "udp"
    username: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._~+-]+$")
    auth_username: str = Field(default="", max_length=64, pattern=r"^[A-Za-z0-9._~+-]*$")
    # Blank keeps the saved one. Asterisk trims surrounding spaces from values.
    password: str = Field(default="", max_length=128, pattern=r"^(?:[!-~](?:[ -~]*[!-~])?)?$")
    registers: bool = True
    ten_digit_prefix: Literal["", "1", "+1"] = ""
    # Hostnames, IP addresses or networks (a.b.c.d/nn) calls in come from, besides the server.
    incoming_from: list[str] = Field(default=[], max_length=32)

    @field_validator("incoming_from")
    @classmethod
    def _sources(cls, sources: list[str]) -> list[str]:
        for source in sources:
            if not _SOURCE.match(source):
                raise ValueError(f"{source!r} isn't a hostname, IP address or network")
        return sources


class SipTrunkSettings(BaseModel):
    server: str
    port: int
    transport: Literal["udp", "tcp"]
    username: str
    auth_username: str
    has_password: bool
    registers: bool
    incoming_from: list[str]
    answers_calls: bool  # Asterisk has the dialplan for calls in (lines saved by older versions don't)


class SipTrunkStatusResponse(BaseModel):
    configured: bool  # the AMI settings are there
    error: Optional[str]
    missing_modules: list[str]
    trunk: Optional[SipTrunkSettings]
    registration: Optional[str]  # Registered, Unregistered, Rejected, ...; None when not registering
    reachability: Optional[str]  # Reachable, Unreachable, ... from Asterisk's OPTIONS pings
    in_use: bool  # the autopatch dial string points at this trunk
    ten_digit_prefix: str
