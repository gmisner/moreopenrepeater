"""APRS-IS client: login and position/status beaconing.

Protocol details confirmed against aprs-is.net's connecting spec, aprslib's
passcode algorithm (rossengeorgiev/aprs-python), and APRS101.PDF's classic
uncompressed position/status packet formats:

- Login line: "user CALLSIGN pass PASSCODE vers SOFTWARENAME VERSION\\r\\n".
- Passcode: a 15-bit checksum derived from the callsign -- not real auth,
  just a token to discourage casual abuse. Verified against the well-known
  reference value ("N0CALL" -> 13023).
- Position report (uncompressed, no timestamp): DTI '!' + lat (DDMM.mmN/S) +
  symbol table char + lon (DDDMM.mmE/W) + symbol code char + free-text comment.
- Status report: DTI '>' + free-text comment (no timestamp, kept simple).

Keep-alive: aprs-is.net's client guidance is to send at most one keep-alive
per 20s and at least one per 24h; servers separately send "#"-prefixed
heartbeat lines roughly every 20s a client can use to detect a dead
connection. Neither is implemented here yet -- the periodic beacon itself
(typically every 10-30 minutes) is the only traffic this client sends.
"""
from __future__ import annotations

import asyncio
from typing import Optional

_PASSCODE_INIT = 0x73E2
_PASSCODE_MASK = 0x7FFF


def compute_passcode(callsign: str) -> int:
    base_callsign = callsign.split("-")[0].upper()
    code = _PASSCODE_INIT
    for index, char in enumerate(base_callsign):
        shift = 8 if index % 2 == 0 else 0
        code ^= ord(char) << shift
    return code & _PASSCODE_MASK


def _format_latitude(lat: float) -> str:
    hemisphere = "N" if lat >= 0 else "S"
    lat = abs(lat)
    degrees = int(lat)
    minutes = (lat - degrees) * 60
    return f"{degrees:02d}{minutes:05.2f}{hemisphere}"


def _format_longitude(lon: float) -> str:
    hemisphere = "E" if lon >= 0 else "W"
    lon = abs(lon)
    degrees = int(lon)
    minutes = (lon - degrees) * 60
    return f"{degrees:03d}{minutes:05.2f}{hemisphere}"


def format_position_report(
    lat: float,
    lon: float,
    comment: str = "",
    symbol_table: str = "/",
    symbol_code: str = "-",
) -> str:
    """Classic uncompressed position report, no timestamp (APRS DTI '!')."""
    return f"!{_format_latitude(lat)}{symbol_table}{_format_longitude(lon)}{symbol_code}{comment}"


def format_status_report(comment: str) -> str:
    """Status report, no timestamp (APRS DTI '>')."""
    return f">{comment}"


class APRSClient:
    def __init__(self, host: str, port: int, callsign: str, passcode: Optional[int] = None) -> None:
        self._host = host
        self._port = port
        self._callsign = callsign
        self._passcode = passcode if passcode is not None else compute_passcode(callsign)
        self._writer: Optional[asyncio.StreamWriter] = None

    async def connect(self, software_name: str = "moreopenrepeater", software_version: str = "0.1") -> None:
        _reader, self._writer = await asyncio.open_connection(self._host, self._port)
        login_line = f"user {self._callsign} pass {self._passcode} vers {software_name} {software_version}\r\n"
        self._writer.write(login_line.encode())
        await self._writer.drain()

    async def send_packet(self, info_field: str) -> None:
        assert self._writer is not None
        packet = f"{self._callsign}>APRS,TCPIP*:{info_field}\r\n"
        self._writer.write(packet.encode())
        await self._writer.drain()

    async def close(self) -> None:
        if self._writer is not None:
            self._writer.close()
            await self._writer.wait_closed()
