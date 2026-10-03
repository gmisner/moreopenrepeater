"""Built-in courtesy and timeout tones, used when no uploaded clip is assigned.

Most styles fill the configured courtesy tone length. The CW letters play at
the station's CW ID speed and pitch, the Nextel chirp keeps its own cadence,
and a custom tone (up to four "hz:ms" segments; 0 Hz is a gap) is as long as
its segments, so those set their own length.
"""
from __future__ import annotations

from typing import Iterable

import numpy as np

from dsp.morse import MORSE_CODE

SCALED_STYLES = ("beep", "high_low", "low_high", "triple", "chirp", "bumblebee", "up_run", "down_run", "bonk", "bee_boo")
CW_STYLES = {"cw_k": "K", "cw_r": "R", "cw_t": "T"}
COURTESY_TONE_STYLES = (*SCALED_STYLES, "nextel", *CW_STYLES, "custom")
# Nextel's Direct Connect talk-permit chirp: 1800 Hz, 30 ms on, 20 off, 30 on, 20 off, 50 on.
_NEXTEL = ((1800, 30), (0, 20), (1800, 30), (0, 20), (1800, 50))
AMPLITUDE = 0.3
_RAMP_SECONDS = 0.005  # fade in/out so tones start and stop without a click

CUSTOM_MAX_SEGMENTS = 4
CUSTOM_HZ = (100, 3000)
CUSTOM_MS = (20, 1000)
DEFAULT_CUSTOM_TONE = "880:100 0:40 1320:100"


def parse_custom_tone(text: str) -> list[tuple[int, int]]:
    """"880:100 0:40 1320:100" as [(hz, ms), ...]; raises ValueError."""
    segments = []
    for part in text.split():
        hz, sep, ms = part.partition(":")
        if not (sep and hz.isdigit() and ms.isdigit()):
            raise ValueError(f"{part!r} isn't hz:ms, like 880:100")
        segments.append((int(hz), int(ms)))
    if not 1 <= len(segments) <= CUSTOM_MAX_SEGMENTS:
        raise ValueError(f"a custom tone has 1 to {CUSTOM_MAX_SEGMENTS} segments")
    for hz, ms in segments:
        if hz and not CUSTOM_HZ[0] <= hz <= CUSTOM_HZ[1]:
            raise ValueError(f"tones are {CUSTOM_HZ[0]} to {CUSTOM_HZ[1]} Hz (0 for a gap)")
        if not CUSTOM_MS[0] <= ms <= CUSTOM_MS[1]:
            raise ValueError(f"segments are {CUSTOM_MS[0]} to {CUSTOM_MS[1]} ms long")
    if not any(hz for hz, _ in segments):
        raise ValueError("a custom tone needs at least one tone, not only gaps")
    return segments


def _fade(wave: np.ndarray, sample_rate: int) -> np.ndarray:
    ramp = min(len(wave) // 2, int(_RAMP_SECONDS * sample_rate))
    if ramp:
        envelope = np.linspace(0.0, 1.0, ramp)
        wave[:ramp] *= envelope
        wave[-ramp:] *= envelope[::-1]
    return wave.astype(np.float32)


def _tone(freq_hz: float, seconds: float, sample_rate: int) -> np.ndarray:
    t = np.arange(max(0, int(round(seconds * sample_rate)))) / sample_rate
    return _fade(AMPLITUDE * np.sin(2 * np.pi * freq_hz * t), sample_rate)


def _sweep(start_hz: float, end_hz: float, seconds: float, sample_rate: int) -> np.ndarray:
    freqs = np.linspace(start_hz, end_hz, max(0, int(round(seconds * sample_rate))))
    return _fade(AMPLITUDE * np.sin(2 * np.pi * np.cumsum(freqs) / sample_rate), sample_rate)


def _silence(seconds: float, sample_rate: int) -> np.ndarray:
    return np.zeros(int(round(seconds * sample_rate)), dtype=np.float32)


def _run(freqs: tuple[float, ...], duration: float, sample_rate: int) -> np.ndarray:
    """Back-to-back tones sharing `duration`, each one exactly its share of the samples."""
    total = int(round(duration * sample_rate))
    edges = np.linspace(0, total, len(freqs) + 1).round().astype(int)
    return np.concatenate([_tone(f, (b - a) / sample_rate, sample_rate) for f, a, b in zip(freqs, edges, edges[1:])])


def _bonk(duration: float, sample_rate: int) -> np.ndarray:
    """A low tone that drops in pitch and dies away, like a Motorola controller's."""
    wave = _sweep(700, 380, duration, sample_rate)
    return _fade(wave * np.exp(-np.linspace(0, 3, len(wave))), sample_rate)


def _cw(letter: str, wpm: float, tone_hz: float, sample_rate: int) -> np.ndarray:
    unit = 1.2 / wpm
    parts = []
    for i, symbol in enumerate(MORSE_CODE[letter]):
        if i:
            parts.append(_silence(unit, sample_rate))
        parts.append(_tone(tone_hz, unit * (1 if symbol == "." else 3), sample_rate))
    return np.concatenate(parts)


def _segments(segments: Iterable[tuple[int, int]], sample_rate: int) -> np.ndarray:
    return np.concatenate([_tone(hz, ms / 1000, sample_rate) if hz else _silence(ms / 1000, sample_rate) for hz, ms in segments])


def _custom(text: str, sample_rate: int) -> np.ndarray:
    try:
        segments = parse_custom_tone(text)
    except ValueError:
        segments = parse_custom_tone(DEFAULT_CUSTOM_TONE)
    return _segments(segments, sample_rate)


def courtesy_tone(
    style: str,
    duration: float,
    sample_rate: int,
    *,
    cw_wpm: float = 20.0,
    cw_tone_hz: float = 800.0,
    custom: str = DEFAULT_CUSTOM_TONE,
) -> np.ndarray:
    """SCALED_STYLES are `duration` long; the rest set their own length."""
    if style == "nextel":
        return _segments(_NEXTEL, sample_rate)
    if style in CW_STYLES:
        return _cw(CW_STYLES[style], cw_wpm, cw_tone_hz, sample_rate)
    if style == "custom":
        return _custom(custom, sample_rate)
    if style == "high_low":
        return _run((1200, 800), duration, sample_rate)
    if style == "low_high":
        return _run((800, 1200), duration, sample_rate)
    if style == "triple":
        beep = _tone(1000, duration / 5, sample_rate)
        gap = _silence(duration / 5, sample_rate)
        return np.concatenate([beep, gap, beep, gap, beep])
    if style == "chirp":
        return _sweep(600, 1600, duration, sample_rate)
    if style == "bumblebee":
        return _run((1000, 1500, 1000, 1500, 1000, 1500), duration, sample_rate)
    if style == "up_run":
        return _run((660, 880, 1320), duration, sample_rate)
    if style == "down_run":
        return _run((1320, 880, 660), duration, sample_rate)
    if style == "bonk":
        return _bonk(duration, sample_rate)
    if style == "bee_boo":
        part = _silence(duration / 6, sample_rate)
        return np.concatenate([_tone(1500, duration * 5 / 12, sample_rate), part, _tone(1000, duration * 5 / 12, sample_rate)])
    return _tone(1000, duration, sample_rate)


def timeout_tone(sample_rate: int) -> np.ndarray:
    """Three descending tones -- the classic "you talked too long" bonk."""
    return np.concatenate([_tone(f, 0.25, sample_rate) for f in (800, 600, 400)])
