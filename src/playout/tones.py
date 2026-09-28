"""Built-in courtesy and timeout tones, used when no uploaded clip is assigned."""
from __future__ import annotations

import numpy as np

COURTESY_TONE_STYLES = ("beep", "high_low", "low_high", "triple", "chirp")
AMPLITUDE = 0.3
_RAMP_SECONDS = 0.005  # fade in/out so tones start and stop without a click


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


def courtesy_tone(style: str, duration: float, sample_rate: int) -> np.ndarray:
    """Total length is `duration` for every style so the controller's
    courtesy_tone state and the audio stay in step."""
    if style == "high_low":
        return np.concatenate([_tone(1200, duration / 2, sample_rate), _tone(800, duration / 2, sample_rate)])
    if style == "low_high":
        return np.concatenate([_tone(800, duration / 2, sample_rate), _tone(1200, duration / 2, sample_rate)])
    if style == "triple":
        beep = _tone(1000, duration / 5, sample_rate)
        gap = _silence(duration / 5, sample_rate)
        return np.concatenate([beep, gap, beep, gap, beep])
    if style == "chirp":
        return _sweep(600, 1600, duration, sample_rate)
    return _tone(1000, duration, sample_rate)


def timeout_tone(sample_rate: int) -> np.ndarray:
    """Three descending tones -- the classic "you talked too long" bonk."""
    return np.concatenate([_tone(f, 0.25, sample_rate) for f in (800, 600, 400)])
