"""Shared Literal aliases, annotated field types, and small validators."""
from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import Field

NetLinks = Literal["leave", "disconnect", "connect"]
WeatherSeverity = Literal["Minor", "Moderate", "Severe", "Extreme"]
CosSource = Literal["vox", "ctcss", "cm108", "gpio", "serial"]
Polarity = Literal["low", "high"]
PttOutput = Literal["cm108", "gpio", "serial"]
LinkRadioPtt = Literal["cm108", "gpio", "serial", "none"]
SerialInputLine = Literal["cts", "dsr", "dcd"]
SerialOutputLine = Literal["rts", "dtr"]
# BCM numbers on the 40-pin header; GPIO0/1 are reserved for HAT EEPROMs.
PiGpioPin = Annotated[int, Field(ge=2, le=27)]
# Only tty device nodes, so a setting can't make the service open some other file.
SerialDevice = Annotated[str, Field(max_length=200, pattern=r"^(/dev/(tty[A-Za-z0-9]+|serial/[A-Za-z0-9._:/+-]+))?$")]
Role = Literal["admin", "operator", "viewer", "listener"]
PublicPageMode = Literal["off", "signed_in", "anyone"]
MonitorSquelch = Literal["vox", "gpio", "open"]
ListenSource = Literal["tx", "rx", "monitor"]  # on air, the repeater's receiver, the monitor receiver
RecordingSource = Literal["repeater", "monitor"]
RecordingSort = Literal["newest", "oldest", "callsign", "callsign_desc"]
TranscriptionEngine = Literal["off", "vosk", "openai"]

# A hostname, or an IPv4/IPv6 address with an optional /prefix, as PJSIP's identify `match` takes.
_SOURCE = re.compile(r"^(?:[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?|[0-9A-Fa-f:.]{2,45})(?:/\d{1,3})?$")

GpioPinNumber = Literal["1", "2", "4", "5", "6", "7", "8"]
FanOutput = Literal["none", "cm108", "gpio"]

NodeNumber = Annotated[str, Field(pattern=r"^[0-9]{1,10}$")]

def hh_mm(value: str) -> str:
    hours, sep, minutes = value.strip().partition(":")
    if not (sep and hours.isdigit() and minutes.isdigit() and int(hours) < 24 and int(minutes) < 60):
        raise ValueError(f"{value!r} is not a HH:MM time")
    return f"{int(hours):02d}:{int(minutes):02d}"


def weekdays(days: list[int]) -> list[int]:
    if any(d < 0 or d > 6 for d in days):
        raise ValueError("days are 0 (Monday) to 6 (Sunday)")
    return sorted(set(days))

MAX_ANNOUNCEMENT_LENGTH = 2000

UpdateChannel = Literal["stable", "beta", "dev"]

_URL_OR_EMPTY = r"^$|^https?://[^\s]+$"
_ONE_LINE = r"^[^\r\n]*$"  # these go into email headers

# `;` starts a comment in Asterisk's config files, so it can't be in a value.
_CONF_TEXT = r"^[^;\\\r\n]*$"

HOMEASSISTANT_TARGET = r"^(event:)?[A-Za-z0-9_.\-]{1,100}$"
