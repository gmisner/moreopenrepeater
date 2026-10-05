"""What each receiver opening sounded like, for tracking down a repeater
that keys up by itself.

While the carrier is up, `OpeningTracker` keeps the peak level, any CTCSS
tone and DTMF digits, and the summed voice-band spectrum. When it drops, the
spectrum gives the strongest frequency and how much of the power sits right
at it: a steady tone (a test tone, an intermod product, the other radio's
courtesy beep) is nearly all one frequency; voice and noise are spread out.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import numpy as np

_BAND_HZ = (200.0, 3500.0)  # above CTCSS, inside a radio's audio passband
_PEAK_BINS = 2  # each side of the strongest bin counted as "at" that frequency


@dataclass(frozen=True)
class ReceiverOpening:
    duration: float  # seconds the carrier was up
    open_db: float  # the level of the block that opened it
    peak_db: float
    strongest_hz: Optional[float]  # None: nothing in the voice band
    tone_share: float  # 0-1: how much of the voice-band power was at strongest_hz
    ctcss_hz: Optional[float]
    dtmf_digits: str
    # Seconds since the repeater's own transmitter unkeyed when it opened;
    # 0.0 = while transmitting, None = not in the last minute.
    after_tx: Optional[float]


class OpeningTracker:
    def __init__(self, sample_rate: int) -> None:
        self._rate = sample_rate
        self.start(0.0, None)

    def start(self, open_db: float, after_tx: Optional[float]) -> None:
        self._samples = 0
        self._open_db = open_db
        self._peak_db = -math.inf
        self._after_tx = after_tx
        self._ctcss: Optional[float] = None
        self._digits: list[str] = []
        self._spectrum: Optional[np.ndarray] = None
        self._window: Optional[np.ndarray] = None

    def feed(self, block: np.ndarray, block_db: float, ctcss_hz: Optional[float], digit: Optional[str]) -> None:
        self._samples += len(block)
        self._peak_db = max(self._peak_db, block_db)
        if self._ctcss is None:
            self._ctcss = ctcss_hz
        if digit is not None:
            self._digits.append(digit)
        if self._window is None or len(self._window) != len(block):
            if self._spectrum is not None:
                return  # the block size changed mid-opening; keep the first size's spectrum
            self._window = np.hanning(len(block))
        power = np.abs(np.fft.rfft(block * self._window)) ** 2
        self._spectrum = power if self._spectrum is None else self._spectrum + power

    def finish(self) -> ReceiverOpening:
        strongest, share = self._strongest()
        return ReceiverOpening(
            duration=self._samples / self._rate,
            open_db=self._open_db,
            peak_db=self._peak_db,
            strongest_hz=strongest,
            tone_share=share,
            ctcss_hz=self._ctcss,
            dtmf_digits="".join(self._digits),
            after_tx=self._after_tx,
        )

    def _strongest(self) -> tuple[Optional[float], float]:
        if self._spectrum is None or self._window is None:
            return None, 0.0
        bin_hz = self._rate / len(self._window)
        low, high = (int(math.ceil(edge / bin_hz)) for edge in _BAND_HZ)
        band = self._spectrum[low : high + 1]
        total = float(band.sum())
        if total <= 1e-12:
            return None, 0.0
        peak = int(np.argmax(band))
        at_peak = float(band[max(0, peak - _PEAK_BINS) : peak + _PEAK_BINS + 1].sum())
        offset = 0.0
        if 0 < peak < len(band) - 1:
            # Fit a parabola through the log power around the peak for a finer estimate than the bin spacing.
            a, b, c = (math.log(max(float(v), 1e-20)) for v in band[peak - 1 : peak + 2])
            if a - 2 * b + c != 0:
                offset = 0.5 * (a - c) / (a - 2 * b + c)
        return (low + peak + offset) * bin_hz, at_peak / total
