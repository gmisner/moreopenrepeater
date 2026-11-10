"""app_rpt / DVSwitch USRP channel protocol (8 kHz PCM + metadata).

With `rxchannel = USRP/<our host>:<our port>:<node port>` in rpt.conf, the
node's "radio" is us. Format from app_rpt's channels/chan_usrp.c, and
confirmed against ASL3 in spikes/usrp_asl3_spike.py: each UDP packet is a
32-byte header -- "USRP" and seven big-endian uint32s (seq, memory, keyup,
talkgroup, type, mpxid, reserved) -- then a type-dependent payload.

  * type 0 (voice): 160 samples (20 ms) of 8 kHz signed-linear PCM
  * type 2 (text): Analog_Bridge metadata — `INFO:…` JSON, or a TLV such as
    SET_INFO with the transmitting DMR Id / callsign / talkgroup (pyUC)

  * To the node: voice packets while our receiver has a signal. chan_usrp
    keys the node's receiver on the first one and unkeys a few frames
    after they stop; it ignores the keyup field we send.
  * From the node / Analog_Bridge: voice packets with keyup=1 while
    transmitting, then a header-only packet with keyup=0 on unkey.
"""
from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from typing import Optional

HEADER = struct.Struct(">4s7I")
EYE = b"USRP"
TYPE_VOICE = 0
TYPE_TEXT = 2
TYPE_TLV = 4
TLV_TAG_SET_INFO = 8
FRAME_SAMPLES = 160
FRAME_BYTES = FRAME_SAMPLES * 2
RATE = 8000


@dataclass(frozen=True)
class Packet:
    seq: int
    keyup: bool
    talkgroup: int
    type: int
    audio: bytes  # FRAME_BYTES of little-endian PCM for voice, else b""
    payload: bytes  # raw bytes after the header


@dataclass(frozen=True)
class SetInfo:
    """Analog_Bridge / pyUC SET_INFO TLV (USRP type TEXT or TLV)."""

    radio_id: int
    talkgroup: int
    slot: int
    color_code: int
    callsign: str
    name: str = ""


def encode_voice(seq: int, pcm: bytes) -> bytes:
    if len(pcm) != FRAME_BYTES:
        raise ValueError(f"a USRP voice frame is {FRAME_BYTES} bytes, not {len(pcm)}")
    return HEADER.pack(EYE, seq & 0xFFFFFFFF, 0, 1, 0, TYPE_VOICE, 0, 0) + pcm


def decode(data: bytes) -> Optional[Packet]:
    """None for anything that isn't a USRP packet."""
    if len(data) < HEADER.size:
        return None
    eye, seq, _memory, keyup, talkgroup, kind, _mpxid, _reserved = HEADER.unpack_from(data)
    if eye != EYE:
        return None
    payload = data[HEADER.size :]
    audio = payload if kind == TYPE_VOICE and len(payload) == FRAME_BYTES else b""
    return Packet(seq=seq, keyup=bool(keyup), talkgroup=talkgroup, type=kind, audio=audio, payload=payload)


def parse_set_info(payload: bytes) -> Optional[SetInfo]:
    """Parse a SET_INFO TLV payload (without the USRP header)."""
    if len(payload) < 15 or payload[0] != TLV_TAG_SET_INFO:
        return None
    radio_id = (payload[2] << 16) + (payload[3] << 8) + payload[4]
    talkgroup = (payload[9] << 16) + (payload[10] << 8) + payload[11]
    slot = payload[12]
    color_code = payload[13] & 0x7F
    callsign = ""
    name = ""
    if payload[14] != 0:
        end = payload.find(b"\x00", 14)
        raw = payload[14:] if end < 0 else payload[14:end]
        text = raw.decode("ascii", errors="replace")
        if text.startswith("{"):
            try:
                obj = json.loads(text)
                callsign = str(obj.get("call") or "")
                name = str(obj.get("name") or "").split(" ")[0]
            except json.JSONDecodeError:
                callsign = text
        else:
            callsign = text
    if not callsign and radio_id:
        callsign = str(radio_id)
    return SetInfo(
        radio_id=radio_id,
        talkgroup=talkgroup,
        slot=slot,
        color_code=color_code,
        callsign=callsign,
        name=name,
    )


def set_info_from_packet(packet: Packet) -> Optional[SetInfo]:
    """SET_INFO may arrive as USRP type TEXT or type TLV."""
    if packet.type not in (TYPE_TEXT, TYPE_TLV):
        return None
    return parse_set_info(packet.payload)
