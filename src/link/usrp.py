"""app_rpt's USRP channel protocol, for carrying a node's radio audio.

With `rxchannel = USRP/<our host>:<our port>:<node port>` in rpt.conf, the
node's "radio" is us. Format from app_rpt's channels/chan_usrp.c, and
confirmed against ASL3 in spikes/usrp_asl3_spike.py: each UDP packet is a
32-byte header -- "USRP" and seven big-endian uint32s (seq, memory, keyup,
talkgroup, type, mpxid, reserved) -- then, for voice, 160 samples (20 ms) of
8 kHz signed-linear audio in host byte order (little-endian here).

  * To the node: voice packets while our receiver has a signal. chan_usrp
    keys the node's receiver on the first one and unkeys it a few frames
    after they stop; it ignores the keyup field we send.
  * From the node: voice packets with keyup=1 while the node transmits,
    then a header-only packet with keyup=0 when it unkeys.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Optional

HEADER = struct.Struct(">4s7I")
EYE = b"USRP"
TYPE_VOICE = 0
FRAME_SAMPLES = 160
FRAME_BYTES = FRAME_SAMPLES * 2
RATE = 8000


@dataclass(frozen=True)
class Packet:
    seq: int
    keyup: bool
    type: int
    audio: bytes  # FRAME_BYTES of little-endian PCM for voice, else b""


def encode_voice(seq: int, pcm: bytes) -> bytes:
    if len(pcm) != FRAME_BYTES:
        raise ValueError(f"a USRP voice frame is {FRAME_BYTES} bytes, not {len(pcm)}")
    return HEADER.pack(EYE, seq & 0xFFFFFFFF, 0, 1, 0, TYPE_VOICE, 0, 0) + pcm


def decode(data: bytes) -> Optional[Packet]:
    """None for anything that isn't a USRP packet."""
    if len(data) < HEADER.size:
        return None
    eye, seq, _memory, keyup, _talkgroup, kind, _mpxid, _reserved = HEADER.unpack_from(data)
    if eye != EYE:
        return None
    audio = data[HEADER.size :]
    if kind != TYPE_VOICE or len(audio) != FRAME_BYTES:
        audio = b""
    return Packet(seq=seq, keyup=bool(keyup), type=kind, audio=audio)
