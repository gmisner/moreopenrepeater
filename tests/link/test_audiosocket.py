import pytest

from link.audiosocket import FrameType, decode_frame, encode_frame, read_frame


class _BufferReader:
    """Fake socket-like object exposing recv(n) over a fixed byte buffer,
    returning at most 2 bytes per call to exercise read_frame's reassembly
    of partial reads."""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pos = 0

    def recv(self, n: int) -> bytes:
        chunk = self._data[self._pos : self._pos + min(n, 2)]
        self._pos += len(chunk)
        return chunk


def test_encode_frame_produces_type_length_payload_header():
    encoded = encode_frame(FrameType.AUDIO, b"\x01\x02\x03\x04")
    assert encoded == bytes([0x10, 0x00, 0x04]) + b"\x01\x02\x03\x04"


def test_encode_hangup_frame_has_empty_payload():
    encoded = encode_frame(FrameType.HANGUP)
    assert encoded == bytes([0x00, 0x00, 0x00])


def test_read_frame_parses_uuid_handshake_across_partial_reads():
    uuid_bytes = bytes(range(16))
    data = encode_frame(FrameType.UUID, uuid_bytes)
    reader = _BufferReader(data)

    frame = read_frame(reader.recv)

    assert frame.type == FrameType.UUID
    assert frame.payload == uuid_bytes


def test_read_frame_raises_on_early_close():
    reader = _BufferReader(bytes([0x10, 0x00, 0x04]))  # header says 4 bytes but none follow

    with pytest.raises(ConnectionError):
        read_frame(reader.recv)


def test_decode_frame_rejects_length_mismatch():
    header = bytes([0x10, 0x00, 0x04])
    with pytest.raises(ValueError):
        decode_frame(header, b"\x01\x02")
