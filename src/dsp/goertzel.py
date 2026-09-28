"""Goertzel-algorithm tone detection/generation for CTCSS and DTMF.

The Goertzel algorithm is used instead of a full FFT because we only ever
need the magnitude at a small, fixed set of known frequencies (39 CTCSS
tones, 8 DTMF row/column tones) per audio block -- much cheaper than an
FFT over the whole spectrum, and it's the standard technique real repeater
controllers (and app_rpt) use for sub-audible tone squelch.

Magnitude is normalized so that a full-scale sinusoid (amplitude 1.0) sitting
exactly on a frequency bin reports a magnitude of ~1.0, regardless of block
size -- this keeps detection thresholds meaningful and stable across
different block sizes.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from .tones import (
    DTMF_COL_FREQUENCIES_HZ,
    DTMF_FREQUENCIES_BY_DIGIT,
    DTMF_ROW_FREQUENCIES_HZ,
    CTCSS_TONES_HZ,
)


def goertzel_magnitude(block: np.ndarray, sample_rate: int, target_freq: float) -> float:
    """Return the normalized Goertzel-algorithm magnitude of target_freq within block."""
    n = len(block)
    k = int(0.5 + n * target_freq / sample_rate)
    omega = 2 * math.pi * k / n
    coeff = 2 * math.cos(omega)

    s_prev = 0.0
    s_prev2 = 0.0
    for sample in block:
        s = float(sample) + coeff * s_prev - s_prev2
        s_prev2 = s_prev
        s_prev = s

    power = s_prev2 * s_prev2 + s_prev * s_prev - coeff * s_prev * s_prev2
    return math.sqrt(max(power, 0.0)) * 2.0 / n


def _goertzel_magnitudes(block: np.ndarray, sample_rate: int, target_freqs) -> list[float]:
    return [goertzel_magnitude(block, sample_rate, f) for f in target_freqs]


@dataclass
class CTCSSDetector:
    """Detects a locked-on CTCSS tone with hysteresis to avoid chatter.

    CTCSS tones are only 2-3 Hz apart (e.g. 165.5 vs 167.9 Hz), so a single
    short audio block doesn't give enough frequency resolution to tell them
    apart -- an FFT/Goertzel's bin resolution is sample_rate/N, and a 320
    sample (40ms @ 8kHz) block only resolves to 25 Hz bins. To get usable
    ~1 Hz resolution, this detector accumulates incoming blocks into a
    rolling `window_seconds`-long buffer and re-analyzes that full window on
    every call, trading detection latency (~`window_seconds`) for accuracy.

    Once a tone is resolved, it must be seen for `lock_blocks` consecutive
    calls before being reported as detected, and absent for `unlock_blocks`
    consecutive calls before being reported as lost -- mirroring how real
    repeater controllers debounce sub-audible tone squelch.
    """

    sample_rate: int
    tones_hz: tuple[float, ...] = CTCSS_TONES_HZ
    magnitude_threshold: float = 0.3
    lock_blocks: int = 3
    unlock_blocks: int = 3
    window_seconds: float = 1.0

    _buffer: np.ndarray = field(default_factory=lambda: np.zeros(0), init=False, repr=False)
    _locked_tone: Optional[float] = field(default=None, init=False, repr=False)
    _candidate_tone: Optional[float] = field(default=None, init=False, repr=False)
    _candidate_count: int = field(default=0, init=False, repr=False)
    _miss_count: int = field(default=0, init=False, repr=False)

    @property
    def _window_samples(self) -> int:
        return int(self.sample_rate * self.window_seconds)

    def process(self, block: np.ndarray) -> Optional[float]:
        self._buffer = np.concatenate([self._buffer, block])[-self._window_samples :]
        if len(self._buffer) < self._window_samples:
            return self._locked_tone  # not enough data yet to resolve tones reliably

        magnitudes = _goertzel_magnitudes(self._buffer, self.sample_rate, self.tones_hz)
        best_index = max(range(len(magnitudes)), key=lambda i: magnitudes[i])
        best_tone = self.tones_hz[best_index]
        best_magnitude = magnitudes[best_index]

        detected = best_tone if best_magnitude >= self.magnitude_threshold else None

        if detected is None:
            self._candidate_tone = None
            self._candidate_count = 0
            if self._locked_tone is not None:
                self._miss_count += 1
                if self._miss_count >= self.unlock_blocks:
                    self._locked_tone = None
                    self._miss_count = 0
            return self._locked_tone

        if detected == self._locked_tone:
            self._miss_count = 0
            return self._locked_tone

        if detected == self._candidate_tone:
            self._candidate_count += 1
        else:
            self._candidate_tone = detected
            self._candidate_count = 1

        if self._candidate_count >= self.lock_blocks:
            self._locked_tone = detected
            self._candidate_tone = None
            self._candidate_count = 0
            self._miss_count = 0

        return self._locked_tone


@dataclass
class DTMFDetector:
    """Detects DTMF digits from row/column Goertzel magnitudes.

    Requires a digit to be present for `press_blocks` consecutive blocks
    before reporting it (debounce), and requires the tone pair to drop out
    before the same digit can be reported again, so a held-down key produces
    one digit event, not a stream of repeats.
    """

    sample_rate: int
    magnitude_threshold: float = 0.3
    twist_ratio_db: float = 8.0
    press_blocks: int = 2

    _candidate_digit: Optional[str] = field(default=None, init=False, repr=False)
    _candidate_count: int = field(default=0, init=False, repr=False)
    _last_reported_digit: Optional[str] = field(default=None, init=False, repr=False)

    def process(self, block: np.ndarray) -> Optional[str]:
        row_magnitudes = _goertzel_magnitudes(block, self.sample_rate, DTMF_ROW_FREQUENCIES_HZ)
        col_magnitudes = _goertzel_magnitudes(block, self.sample_rate, DTMF_COL_FREQUENCIES_HZ)

        best_row = max(range(4), key=lambda i: row_magnitudes[i])
        best_col = max(range(4), key=lambda i: col_magnitudes[i])

        digit = None
        if (
            row_magnitudes[best_row] >= self.magnitude_threshold
            and col_magnitudes[best_col] >= self.magnitude_threshold
        ):
            twist_db = 20 * math.log10(col_magnitudes[best_col] / row_magnitudes[best_row])
            if abs(twist_db) <= self.twist_ratio_db:
                row_freq = DTMF_ROW_FREQUENCIES_HZ[best_row]
                col_freq = DTMF_COL_FREQUENCIES_HZ[best_col]
                for candidate, freqs in DTMF_FREQUENCIES_BY_DIGIT.items():
                    if freqs == (row_freq, col_freq):
                        digit = candidate
                        break

        if digit is None:
            self._candidate_digit = None
            self._candidate_count = 0
            self._last_reported_digit = None
            return None

        if digit == self._last_reported_digit:
            return None  # still holding the same key -- already reported

        if digit == self._candidate_digit:
            self._candidate_count += 1
        else:
            self._candidate_digit = digit
            self._candidate_count = 1

        if self._candidate_count >= self.press_blocks:
            self._last_reported_digit = digit
            self._candidate_digit = None
            self._candidate_count = 0
            return digit

        return None


def ctcss_tone(freq_hz: float, sample_rate: int, num_samples: int, amplitude: float = 0.1) -> np.ndarray:
    """Generate a sub-audible CTCSS tone to overlay on outgoing transmit audio."""
    t = np.arange(num_samples) / sample_rate
    return amplitude * np.sin(2 * math.pi * freq_hz * t)


def dtmf_tone(digit: str, sample_rate: int, num_samples: int, amplitude: float = 0.3) -> np.ndarray:
    """Generate a standard dual-tone DTMF burst for the given keypad digit."""
    row_freq, col_freq = DTMF_FREQUENCIES_BY_DIGIT[digit]
    t = np.arange(num_samples) / sample_rate
    return amplitude * (np.sin(2 * math.pi * row_freq * t) + np.sin(2 * math.pi * col_freq * t)) / 2
