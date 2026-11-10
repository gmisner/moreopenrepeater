import struct

import pytest

from link.usrp import (
    FRAME_BYTES,
    HEADER,
    TYPE_TEXT,
    TYPE_TLV,
    TLV_TAG_SET_INFO,
    decode,
    encode_voice,
    parse_set_info,
    set_info_from_packet,
)


def test_voice_round_trips():
    pcm = bytes(range(256)) + bytes(FRAME_BYTES - 256)
    packet = decode(encode_voice(7, pcm))
    assert (packet.seq, packet.keyup, packet.type, packet.audio, packet.talkgroup) == (7, True, 0, pcm, 0)
    assert packet.payload == pcm


def test_header_is_big_endian_after_the_eye():
    data = encode_voice(0x01020304, bytes(FRAME_BYTES))
    assert data[:8] == b"USRP\x01\x02\x03\x04"
    assert len(data) == HEADER.size + FRAME_BYTES


def test_frames_must_be_20_ms():
    with pytest.raises(ValueError):
        encode_voice(1, bytes(10))


def test_header_only_unkey():
    packet = decode(HEADER.pack(b"USRP", 9, 0, 0, 0, 0, 0, 0))
    assert not packet.keyup and packet.audio == b"" and packet.payload == b""


def test_non_voice_and_foreign_packets():
    text = decode(HEADER.pack(b"USRP", 1, 0, 1, 0, TYPE_TEXT, 0, 0) + b"text")
    assert text is not None and text.audio == b"" and text.payload == b"text"
    assert decode(b"RTP?" + bytes(28 + FRAME_BYTES)) is None
    assert decode(b"USRP") is None
    assert decode(struct.pack(">4s7I", b"USRP", 1, 0, 1, 0, 0, 0, 0) + bytes(100)).audio == b""


def _set_info_payload(radio_id: int, talkgroup: int, slot: int, callsign: str, color_code: int = 1) -> bytes:
    call = callsign.encode("ascii") + b"\x00"
    body = bytearray(14 + len(call))
    body[0] = TLV_TAG_SET_INFO
    body[1] = len(body) - 2
    body[2] = (radio_id >> 16) & 0xFF
    body[3] = (radio_id >> 8) & 0xFF
    body[4] = radio_id & 0xFF
    body[9] = (talkgroup >> 16) & 0xFF
    body[10] = (talkgroup >> 8) & 0xFF
    body[11] = talkgroup & 0xFF
    body[12] = slot
    body[13] = color_code
    body[14:] = call
    return bytes(body)


def test_parse_set_info_callsign():
    info = parse_set_info(_set_info_payload(3_120_001, 3100, 2, "W1AW"))
    assert info is not None
    assert (info.radio_id, info.talkgroup, info.slot, info.callsign, info.name) == (3_120_001, 3100, 2, "W1AW", "")


def test_parse_set_info_json_callsign():
    payload = _set_info_payload(99, 9, 1, '{"call":"K1ABC","name":"Alice Smith"}')
    info = parse_set_info(payload)
    assert info is not None
    assert info.callsign == "K1ABC" and info.name == "Alice"


def test_set_info_from_text_and_tlv_packets():
    payload = _set_info_payload(1, 91, 2, "N0CALL")
    text = decode(HEADER.pack(b"USRP", 1, 0, 0, 0, TYPE_TEXT, 0, 0) + payload)
    tlv = decode(HEADER.pack(b"USRP", 2, 0, 0, 0, TYPE_TLV, 0, 0) + payload)
    assert text is not None and tlv is not None
    assert set_info_from_packet(text).callsign == "N0CALL"
    assert set_info_from_packet(tlv).talkgroup == 91
    voice = decode(encode_voice(3, bytes(FRAME_BYTES)))
    assert set_info_from_packet(voice) is None
