import struct

import pytest

from link.usrp import FRAME_BYTES, HEADER, decode, encode_voice


def test_voice_round_trips():
    pcm = bytes(range(256)) + bytes(FRAME_BYTES - 256)
    packet = decode(encode_voice(7, pcm))
    assert (packet.seq, packet.keyup, packet.type, packet.audio) == (7, True, 0, pcm)


def test_header_is_big_endian_after_the_eye():
    data = encode_voice(0x01020304, bytes(FRAME_BYTES))
    assert data[:8] == b"USRP\x01\x02\x03\x04"
    assert len(data) == HEADER.size + FRAME_BYTES


def test_frames_must_be_20_ms():
    with pytest.raises(ValueError):
        encode_voice(1, bytes(10))


def test_header_only_unkey():
    packet = decode(HEADER.pack(b"USRP", 9, 0, 0, 0, 0, 0, 0))
    assert not packet.keyup and packet.audio == b""


def test_non_voice_and_foreign_packets():
    assert decode(HEADER.pack(b"USRP", 1, 0, 1, 0, 2, 0, 0) + b"text").audio == b""
    assert decode(b"RTP?" + bytes(28 + FRAME_BYTES)) is None
    assert decode(b"USRP") is None
    assert decode(struct.pack(">4s7I", b"USRP", 1, 0, 1, 0, 0, 0, 0) + bytes(100)).audio == b""
