"""Asterisk AudioSocket protocol framing.

Wire format confirmed against Asterisk's res_audiosocket.h/.c and
app_audiosocket.c (docs.asterisk.org/Configuration/Channel-Drivers/AudioSocket):
a 3-byte header -- 1-byte type, 2-byte big-endian length -- followed by that
many payload bytes. Asterisk is always the connection initiator and sends a
16-byte UUID frame first to identify which call this socket belongs to;
audio frames (type AUDIO) carry 8kHz 16-bit signed-linear mono PCM,
little-endian samples, sized to Asterisk's internal 20ms frame (320 bytes).

Asterisk tears the call down if neither side produces activity on the
socket for ~2s (app_audiosocket.c's MAX_WAIT_TIMEOUT_MSEC) -- callers must
keep sending audio or otherwise keep the connection active.
"""
from __future__ import annotations

import asyncio
import struct
from dataclasses import dataclass
from enum import IntEnum
from typing import Callable


class FrameType(IntEnum):
    HANGUP = 0x00
    UUID = 0x01
    DTMF = 0x03
    AUDIO = 0x10  # 8kHz 16-bit signed-linear mono PCM, little-endian samples
    ERROR = 0xFF


_HEADER = struct.Struct(">BH")  # type byte, big-endian uint16 length
HEADER_SIZE = _HEADER.size


@dataclass(frozen=True)
class Frame:
    type: FrameType
    payload: bytes

    def encode(self) -> bytes:
        return _HEADER.pack(self.type, len(self.payload)) + self.payload


def encode_frame(frame_type: FrameType, payload: bytes = b"") -> bytes:
    return Frame(frame_type, payload).encode()


def decode_frame(header: bytes, payload: bytes) -> Frame:
    frame_type, length = _HEADER.unpack(header)
    if length != len(payload):
        raise ValueError(f"AudioSocket frame declared length {length} but got {len(payload)} bytes")
    return Frame(FrameType(frame_type), payload)


def read_frame(recv: Callable[[int], bytes]) -> Frame:
    """Read one frame using a caller-supplied `recv(n) -> bytes` function
    (e.g. a bound `socket.recv`), handling the header/payload split and
    partial reads.
    """
    header = _recv_exact(recv, HEADER_SIZE)
    _, length = _HEADER.unpack(header)
    payload = _recv_exact(recv, length) if length else b""
    return decode_frame(header, payload)


async def read_frame_async(reader: asyncio.StreamReader) -> Frame:
    """`read_frame` for asyncio streams; raises IncompleteReadError at EOF."""
    header = await reader.readexactly(HEADER_SIZE)
    _, length = _HEADER.unpack(header)
    payload = await reader.readexactly(length) if length else b""
    return decode_frame(header, payload)


def _recv_exact(recv: Callable[[int], bytes], n: int) -> bytes:
    chunks: list[bytes] = []
    remaining = n
    while remaining > 0:
        chunk = recv(remaining)
        if not chunk:
            raise ConnectionError("AudioSocket connection closed while reading a frame")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)
