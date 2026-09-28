"""Block-by-block sample-rate conversion for the live audio path.

Sound cards often only run at 44.1/48 kHz (a CM108 on a Pi's raw ALSA
device can't do 16 kHz), while all detection and playout runs at 16 kHz.
Unlike `playout.wav.resample`, which converts a whole clip at once, this
keeps filter history and the fractional read position between calls, so
converting a stream in 20 ms pieces gives the same result as converting it
in one go -- no clicks at block boundaries.

Quality target is two-way-radio voice (300-3000 Hz): a 63-tap windowed-sinc
low-pass against aliasing/imaging, and linear interpolation between samples.
"""
from __future__ import annotations

import numpy as np

from playout.wav import lowpass_kernel


class StreamFIR:
    def __init__(self, kernel: np.ndarray) -> None:
        self._kernel = kernel
        self._history = np.zeros(len(kernel) - 1)

    def process(self, block: np.ndarray) -> np.ndarray:
        padded = np.concatenate([self._history, block])
        self._history = padded[len(padded) - len(self._history) :]
        return np.convolve(padded, self._kernel, mode="valid")


class StreamResampler:
    def __init__(self, from_rate: int, to_rate: int) -> None:
        self.from_rate = from_rate
        self.to_rate = to_rate
        self._step = from_rate / to_rate
        self._pre = StreamFIR(lowpass_kernel(0.45 * to_rate / from_rate)) if to_rate < from_rate else None
        self._post = StreamFIR(lowpass_kernel(0.45 * from_rate / to_rate)) if to_rate > from_rate else None
        self._last = 0.0
        # Next output position, in input samples, where 0 is the previous
        # call's final sample -- so 1.0 is the first sample of this call.
        self._position = 1.0

    @property
    def passthrough(self) -> bool:
        return self.from_rate == self.to_rate

    def process(self, block: np.ndarray) -> np.ndarray:
        block = np.asarray(block, dtype=np.float64).reshape(-1)
        if self.passthrough:
            return block.astype(np.float32)
        if self._pre is not None:
            block = self._pre.process(block)
        x = np.concatenate([[self._last], block])
        end = len(x) - 1
        if self._position > end:
            out = np.zeros(0)
        else:
            count = int(np.floor((end - self._position) / self._step)) + 1
            positions = self._position + self._step * np.arange(count)
            out = np.interp(positions, np.arange(len(x)), x)
            self._position = positions[-1] + self._step
        self._position -= end
        self._last = x[-1]
        if self._post is not None:
            out = self._post.process(out)
        return out.astype(np.float32)
