"""FM pre-emphasis / de-emphasis: 6 dB per octave between 300 and 3000 Hz.

A radio's speaker and mic audio already has this applied, but discriminator
("flat") receive audio and a flat transmit input don't. These kernels put it
back, with 0 dB at 1 kHz so levels set with a 1 kHz tone don't move.
Linear-phase FIRs (numpy has no IIR filter) designed by frequency sampling.
"""
from __future__ import annotations

import numpy as np

LOW_CORNER_HZ = 300.0
HIGH_CORNER_HZ = 3000.0
REFERENCE_HZ = 1000.0
_KERNEL_SECONDS = 0.02
_DESIGN_POINTS = 1 << 14


def preemphasis_gain(freq_hz):
    """The ideal response: rising 6 dB/octave from 300 Hz, levelling off at 3 kHz."""

    def raw(f):
        f = np.asarray(f, dtype=np.float64)
        return np.sqrt(1 + (f / LOW_CORNER_HZ) ** 2) / np.sqrt(1 + (f / HIGH_CORNER_HZ) ** 2)

    return raw(freq_hz) / raw(REFERENCE_HZ)


def emphasis_kernel(sample_rate: int, pre: bool) -> np.ndarray:
    taps = 2 * int(sample_rate * _KERNEL_SECONDS / 2) + 1
    freqs = np.fft.rfftfreq(_DESIGN_POINTS, 1 / sample_rate)
    gain = preemphasis_gain(freqs)
    impulse = np.fft.irfft(gain if pre else 1 / gain, _DESIGN_POINTS)
    half = taps // 2
    kernel = np.concatenate([impulse[-half:], impulse[: half + 1]]) * np.blackman(taps)
    return kernel.astype(np.float64)
