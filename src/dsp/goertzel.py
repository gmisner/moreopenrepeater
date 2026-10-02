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

import functools
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


@functools.lru_cache(maxsize=16)
def _bin_basis(n: int, sample_rate: int, target_freqs: tuple[float, ...]) -> np.ndarray:
    ks = np.floor(0.5 + n * np.asarray(target_freqs) / sample_rate)
    return np.exp(-2j * np.pi * np.outer(ks, np.arange(n)) / n)


def _goertzel_magnitudes(block: np.ndarray, sample_rate: int, target_freqs) -> list[float]:
    """Same result as `goertzel_magnitude` per frequency -- a Goertzel filter's
    output power *is* the DFT bin's |X[k]|^2 -- but as one cached-matrix
    product instead of a Python loop per sample."""
    n = len(block)
    basis = _bin_basis(n, sample_rate, tuple(target_freqs))
    return list(np.abs(basis @ np.asarray(block, dtype=np.float64)) * 2.0 / n)


@functools.lru_cache(maxsize=16)
def _window_basis(length: int, window: int, ks: tuple[float, ...]) -> np.ndarray:
    return np.exp(-2j * np.pi * np.outer(ks, np.arange(length)) / window)


@dataclass
class CTCSSDetector:
    """Detects a locked-on CTCSS tone with hysteresis to avoid chatter.

    CTCSS tones are only 2-3 Hz apart (e.g. 165.5 vs 167.9 Hz), so a single
    short audio block doesn't give enough frequency resolution to tell them
    apart -- an FFT/Goertzel's bin resolution is sample_rate/N, and a 320
    sample (40ms @ 8kHz) block only resolves to 25 Hz bins. To get usable
    ~1 Hz resolution, this detector accumulates incoming blocks into a
    rolling `window_seconds`-long buffer, trading detection latency
    (~`window_seconds`) for accuracy.

    Re-analyzing the whole window (1 s x 50 tones) on every call is too slow
    for a Raspberry Pi 3, so the window's DFT bins are kept as running sums:
    each `feed` adds the new samples' terms and subtracts the terms of the
    samples leaving the window. Terms use each sample's position modulo the
    window length, and the bins are whole cycles per window, so the sums
    differ from a fresh DFT of the window only by a unit-magnitude phase
    per bin -- the magnitudes are the same.

    Once a tone is resolved, it must be seen for `lock_blocks` consecutive
    `decide` calls before being reported as detected, and absent for
    `unlock_blocks` consecutive calls before being reported as lost --
    mirroring how real repeater controllers debounce sub-audible tone squelch.
    """

    sample_rate: int
    tones_hz: tuple[float, ...] = CTCSS_TONES_HZ
    magnitude_threshold: float = 0.3
    lock_blocks: int = 3
    unlock_blocks: int = 3
    window_seconds: float = 1.0

    _buffer: np.ndarray = field(default_factory=lambda: np.zeros(0), init=False, repr=False)
    _position: int = field(default=0, init=False, repr=False)
    _bins: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.complex128), init=False, repr=False)
    _locked_tone: Optional[float] = field(default=None, init=False, repr=False)
    _candidate_tone: Optional[float] = field(default=None, init=False, repr=False)
    _candidate_count: int = field(default=0, init=False, repr=False)
    _miss_count: int = field(default=0, init=False, repr=False)

    def __post_init__(self) -> None:
        self._window = int(self.sample_rate * self.window_seconds)
        self._ks = tuple(float(k) for k in np.floor(0.5 + self._window * np.asarray(self.tones_hz) / self.sample_rate))
        self._bins = np.zeros(len(self.tones_hz), dtype=np.complex128)

    def _terms(self, samples: np.ndarray, position: int) -> np.ndarray:
        phase = np.exp(-2j * np.pi * np.asarray(self._ks) * position / self._window)
        return phase * (_window_basis(len(samples), self._window, self._ks) @ samples)

    def feed(self, block: np.ndarray) -> None:
        """Add samples to the window; cheap enough to call on every block."""
        block = np.asarray(block, dtype=np.float64).reshape(-1)
        window = self._window
        if len(block) >= window:
            self._position = (self._position + len(block) - window) % window
            self._buffer = block[-window:].copy()
            self._bins = self._terms(self._buffer, self._position)
            return
        self._bins += self._terms(block, self._position)
        overflow = len(self._buffer) + len(block) - window
        if overflow > 0:
            start = (self._position - len(self._buffer)) % window
            self._bins -= self._terms(self._buffer[:overflow], start)
        self._buffer = np.concatenate([self._buffer, block])[-window:]
        self._position = (self._position + len(block)) % window

    def process(self, block: np.ndarray) -> Optional[float]:
        self.feed(block)
        return self.decide()

    def decide(self) -> Optional[float]:
        if len(self._buffer) < self._window:
            return self._locked_tone  # not enough data yet to resolve tones reliably

        magnitudes = list(np.abs(self._bins) * 2.0 / self._window)
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

    @property
    def holding(self) -> Optional[str]:
        """The reported digit whose tones are still present in the last block."""
        return self._last_reported_digit

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
