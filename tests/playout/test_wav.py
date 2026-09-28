import struct

import numpy as np
import pytest

from playout.wav import WavError, decode_wav, encode_wav, resample


def _wav(format_tag: int, bits: int, channels: int, rate: int, payload: bytes, extensible: bool = False) -> bytes:
    block_align = channels * bits // 8
    if extensible:
        fmt = struct.pack("<HHIIHHHHI", 0xFFFE, channels, rate, rate * block_align, block_align, bits, 22, bits, 0)
        fmt += struct.pack("<H", format_tag) + b"\x00\x00\x00\x00\x10\x00\x80\x00\x00\xaa\x00\x38\x9b\x71"
    else:
        fmt = struct.pack("<HHIIHH", format_tag, channels, rate, rate * block_align, block_align, bits)
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(payload)) + payload
    return b"RIFF" + struct.pack("<I", len(body)) + body


def test_encode_then_decode_round_trips_within_16_bit_precision():
    samples = np.sin(np.linspace(0, 20, 800)).astype(np.float32) * 0.5
    decoded, rate = decode_wav(encode_wav(samples, 8000))
    assert rate == 8000
    assert np.allclose(decoded, samples, atol=1e-4)


def test_decodes_32_bit_float():
    payload = np.array([0.5, -0.25], dtype="<f4").tobytes()
    decoded, _ = decode_wav(_wav(3, 32, 1, 8000, payload))
    assert decoded.tolist() == [0.5, -0.25]


def test_decodes_24_bit_pcm_including_negative_values():
    # +0.5 and -0.5 full scale as little-endian 24-bit ints.
    payload = (0x400000).to_bytes(3, "little") + (0x1000000 - 0x400000).to_bytes(3, "little")
    decoded, _ = decode_wav(_wav(1, 24, 1, 8000, payload))
    assert np.allclose(decoded, [0.5, -0.5])


def test_decodes_wave_format_extensible():
    payload = np.array([1000, -1000], dtype="<i2").tobytes()
    decoded, _ = decode_wav(_wav(1, 16, 1, 8000, payload, extensible=True))
    assert np.allclose(decoded, [1000 / 32768, -1000 / 32768])


def test_downmixes_stereo_to_mono():
    payload = np.array([16384, 0, 16384, 0], dtype="<i2").tobytes()  # L=0.5, R=0 per frame
    decoded, _ = decode_wav(_wav(1, 16, 2, 8000, payload))
    assert np.allclose(decoded, [0.25, 0.25])


@pytest.mark.parametrize("data", [b"", b"not a wav at all", b"RIFF\x00\x00\x00\x00WAVE"])
def test_rejects_garbage(data):
    with pytest.raises(WavError):
        decode_wav(data)


def test_resample_changes_length_proportionally():
    assert len(resample(np.zeros(44100, dtype=np.float32), 44100, 16000)) == 16000


def test_downsampling_filters_out_content_above_the_new_nyquist():
    rate = 48000
    t = np.arange(rate) / rate
    above_nyquist = np.sin(2 * np.pi * 7000 * t).astype(np.float32)  # aliases to 1 kHz at 8 kHz if unfiltered

    out = resample(above_nyquist, rate, 8000)

    assert np.max(np.abs(out[200:-200])) < 0.1
