"""Minimal WAV decode/encode plus resampling, numpy-only.

The stdlib `wave` module only handles integer PCM, but clips exported from
Audacity and friends are often 32-bit float (format tag 3) or wrapped in
WAVE_FORMAT_EXTENSIBLE, so this parses the RIFF chunks itself.
"""
from __future__ import annotations

import io
import math
import struct
from pathlib import Path
from typing import Union

import numpy as np

_FORMAT_PCM = 1
_FORMAT_FLOAT = 3
_FORMAT_EXTENSIBLE = 0xFFFE


class WavError(ValueError):
    pass


def decode_wav(data: bytes) -> tuple[np.ndarray, int]:
    """Return (mono float32 samples in [-1, 1], sample_rate)."""
    if len(data) < 12 or data[0:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise WavError("not a RIFF/WAVE file")
    fmt = None
    pcm = None
    offset = 12
    while offset + 8 <= len(data):
        chunk_id = data[offset : offset + 4]
        (size,) = struct.unpack_from("<I", data, offset + 4)
        body = data[offset + 8 : offset + 8 + size]
        if chunk_id == b"fmt ":
            fmt = body
        elif chunk_id == b"data":
            pcm = body
        offset += 8 + size + (size & 1)  # chunks are word-aligned
    if fmt is None or pcm is None or len(fmt) < 16:
        raise WavError("missing fmt or data chunk")

    format_tag, channels, sample_rate, _, block_align, bits = struct.unpack_from("<HHIIHH", fmt)
    if format_tag == _FORMAT_EXTENSIBLE and len(fmt) >= 26:
        (format_tag,) = struct.unpack_from("<H", fmt, 24)  # first 2 bytes of the SubFormat GUID
    if channels < 1 or block_align < 1:
        raise WavError("invalid channel count")

    usable = len(pcm) - len(pcm) % block_align
    raw = pcm[:usable]
    if format_tag == _FORMAT_FLOAT and bits in (32, 64):
        samples = np.frombuffer(raw, dtype="<f4" if bits == 32 else "<f8").astype(np.float32)
    elif format_tag == _FORMAT_PCM and bits == 8:
        samples = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif format_tag == _FORMAT_PCM and bits == 16:
        samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif format_tag == _FORMAT_PCM and bits == 24:
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        ints = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        ints = np.where(ints & 0x800000, ints - 0x1000000, ints)
        samples = ints.astype(np.float32) / 8388608.0
    elif format_tag == _FORMAT_PCM and bits == 32:
        samples = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    else:
        raise WavError(f"unsupported WAV encoding (format {format_tag}, {bits}-bit)")

    samples = samples.reshape(-1, channels).mean(axis=1)
    return np.clip(samples, -1.0, 1.0).astype(np.float32), sample_rate


def read_wav(path: Union[str, Path], target_rate: int) -> np.ndarray:
    samples, rate = decode_wav(Path(path).read_bytes())
    return resample(samples, rate, target_rate)


def encode_wav(samples: np.ndarray, sample_rate: int) -> bytes:
    """16-bit mono PCM -- what every browser `<audio>` element plays."""
    ints = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2")
    buf = io.BytesIO()
    buf.write(b"RIFF" + struct.pack("<I", 36 + ints.nbytes) + b"WAVE")
    buf.write(b"fmt " + struct.pack("<IHHIIHH", 16, _FORMAT_PCM, 1, sample_rate, sample_rate * 2, 2, 16))
    buf.write(b"data" + struct.pack("<I", ints.nbytes) + ints.tobytes())
    return buf.getvalue()


def lowpass_kernel(cutoff_ratio: float, taps: int = 63) -> np.ndarray:
    """Windowed-sinc FIR; cutoff_ratio is cutoff / sample_rate."""
    n = np.arange(taps) - (taps - 1) / 2
    kernel = np.sinc(2 * cutoff_ratio * n) * np.hamming(taps)
    return kernel / kernel.sum()


def resample(samples: np.ndarray, from_rate: int, to_rate: int) -> np.ndarray:
    if from_rate == to_rate or len(samples) == 0:
        return samples.astype(np.float32)
    if to_rate < from_rate:
        # Low-pass below the new Nyquist first, or content above it aliases
        # back down as audible garbage (plain interpolation doesn't do this).
        samples = np.convolve(samples, lowpass_kernel(0.45 * to_rate / from_rate), mode="same")
    out_len = max(1, int(math.floor(len(samples) * to_rate / from_rate)))
    positions = np.arange(out_len) * (from_rate / to_rate)
    return np.interp(positions, np.arange(len(samples)), samples).astype(np.float32)
