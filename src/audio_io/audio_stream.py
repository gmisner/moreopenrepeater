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
    dropped_input_blocks: int = field(default=0, init=False)
    starved_output_blocks: int = field(default=0, init=False)

    def process(self, indata: np.ndarray, outdata: np.ndarray, frames: int, time, status) -> None:
        try:
            self.input_queue.put_nowait(indata.copy())
        except queue.Full:
            self.dropped_input_blocks += 1

        try:
            outdata[:] = self.output_queue.get_nowait()
        except queue.Empty:
            outdata.fill(0)
            self.starved_output_blocks += 1


class AudioStream:
    """Owns the PortAudio duplex stream lifecycle."""

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
        self._pump = AudioBlockPump(input_queue, output_queue, block_size)
        self._stream = sd.Stream(
            samplerate=sample_rate,
            blocksize=block_size,
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
