"""Real-time audio I/O via sounddevice/PortAudio.

The PortAudio callback runs in a high-priority native thread that must never
block, allocate heavily, or touch Python-level I/O/locks that a lower-
priority thread might be holding. So the callback logic here (AudioBlockPump)
only does non-blocking numpy queue puts/gets -- all DSP, network, and
hardware GPIO work happens on other threads that consume/produce these
queues, matching the fixed 20-40ms blocksize used throughout the project.
"""
from __future__ import annotations

import queue
from dataclasses import dataclass, field

import numpy as np

try:
    import sounddevice as sd
except (ImportError, OSError):
    sd = None  # sounddevice/PortAudio isn't installed/available on this system


@dataclass
class AudioBlockPump:
    """The callback logic itself, kept separate from stream lifecycle so it
    can be unit tested by calling `process(...)` directly with plain numpy
    arrays and queues -- no PortAudio device required.
    """

    input_queue: "queue.Queue[np.ndarray]"
    output_queue: "queue.Queue[np.ndarray]"
    block_size: int
    dropped_input_blocks: int = field(default=0, init=False)  # the worker fell behind
    starved_output_blocks: int = field(default=0, init=False)  # no transmit audio ready in time
    input_overflows: int = field(default=0, init=False)  # the sound card's own receive overrun (xrun)
    output_underflows: int = field(default=0, init=False)  # its transmit underrun (xrun)

    def process(self, indata: np.ndarray, outdata: np.ndarray, frames: int, time, status) -> None:
        if status:
            if status.input_overflow:
                self.input_overflows += 1
            if status.output_underflow:
                self.output_underflows += 1
        try:
            self.input_queue.put_nowait(indata.copy())
        except queue.Full:
            self.dropped_input_blocks += 1

        try:
            outdata[:] = self.output_queue.get_nowait()
        except queue.Empty:
            outdata.fill(0)
            self.starved_output_blocks += 1


_FALLBACK_RATES = (48000, 44100)


def pick_stream_rate(device, preferred: int, channels: int = 1) -> int:
    """The first rate both halves of a duplex stream accept: `preferred`,
    then each device's own default, then common hardware rates. Raw ALSA
    devices (a CM108 on a Pi) reject rates the chip can't run natively;
    CoreAudio and PulseAudio/PipeWire convert, so they take `preferred`."""
    input_device, output_device = device if isinstance(device, tuple) else (device, device)
    candidates = [preferred]
    for dev, kind in ((input_device, "input"), (output_device, "output")):
        try:
            candidates.append(int(sd.query_devices(dev, kind)["default_samplerate"]))
        except (ValueError, sd.PortAudioError):
            pass
    candidates += _FALLBACK_RATES
    for rate in dict.fromkeys(candidates):
        try:
            sd.check_input_settings(input_device, channels=channels, dtype="float32", samplerate=rate)
            sd.check_output_settings(output_device, channels=channels, dtype="float32", samplerate=rate)
        except (ValueError, sd.PortAudioError):
            continue
        return rate
    raise RuntimeError(f"no sample rate works for both input and output (tried {list(dict.fromkeys(candidates))})")


class AudioStream:
    """Owns the PortAudio duplex stream lifecycle.

    `sample_rate`/`block_size` are what the caller would like; the stream may
    run at a different rate the devices support, exposed as `sample_rate`
    and `block_size` (same block duration) for the caller to resample.
    """

    def __init__(
        self,
        sample_rate: int,
        block_size: int,
        input_queue: "queue.Queue[np.ndarray]",
        output_queue: "queue.Queue[np.ndarray]",
        channels: int = 1,
        device=None,
    ) -> None:
        if sd is None:
            raise RuntimeError("sounddevice/PortAudio is not available on this system")
        self.sample_rate = pick_stream_rate(device, sample_rate, channels)
        self.block_size = round(block_size * self.sample_rate / sample_rate)
        self._pump = AudioBlockPump(input_queue, output_queue, self.block_size)
        self._stream = sd.Stream(
            samplerate=self.sample_rate,
            blocksize=self.block_size,
            channels=channels,
            dtype="float32",
            device=device,
            callback=self._pump.process,
        )

    def start(self) -> None:
        self._stream.start()

    def stop(self) -> None:
        self._stream.stop()
        self._stream.close()

    @property
    def dropped_input_blocks(self) -> int:
        return self._pump.dropped_input_blocks

    @property
    def starved_output_blocks(self) -> int:
        return self._pump.starved_output_blocks

    @property
    def input_overflows(self) -> int:
        return self._pump.input_overflows

    @property
    def output_underflows(self) -> int:
        return self._pump.output_underflows
