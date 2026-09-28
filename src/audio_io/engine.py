"""Runs an AudioProcessor against real audio devices.

Threads:
  - PortAudio's callback thread only moves blocks between queues
    (`AudioBlockPump`) -- it must never block.
  - A worker thread takes each receive block, runs `AudioProcessor.process`,
    queues the transmit block, drives the PTT output when "transmitting"
    changes, and hands controller events to `on_events` (which must be
    thread-safe -- the API layer uses `loop.call_soon_threadsafe`).
  - Optionally, a thread polling a hardware COS input (CM108 GPIO), since
    reading it can block.
"""
from __future__ import annotations

import logging
import queue
import threading
from typing import Callable, Optional

import numpy as np

from controller.events import ControllerEvent

from .audio_stream import AudioStream
from .processor import AudioProcessor

_logger = logging.getLogger("moreopenrepeater.audio")

QUEUE_BLOCKS = 10
PRIME_BLOCKS = 2  # output cushion so the first callbacks aren't starved
COS_POLL_SECONDS = 0.02


class AudioEngine:
    def __init__(
        self,
        processor: AudioProcessor,
        block_size: int,
        on_events: Callable[[list[ControllerEvent]], None],
        input_device: Optional[str] = None,
        output_device: Optional[str] = None,
        ptt_output: Optional[Callable[[bool], None]] = None,
        cos_input: Optional[Callable[[], bool]] = None,
        stream_factory: Callable[..., AudioStream] = AudioStream,
    ) -> None:
        self.processor = processor
        self.block_size = block_size
        self._on_events = on_events
        self._input_device = input_device
        self._output_device = output_device
        self._ptt_output = ptt_output
        self._cos_input = cos_input
        self._stream_factory = stream_factory
        self._input: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=QUEUE_BLOCKS)
        self._output: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=QUEUE_BLOCKS)
        self._stream: Optional[AudioStream] = None
        self._threads: list[threading.Thread] = []
        self._stop = threading.Event()
        self.transmitting = False

    @property
    def running(self) -> bool:
        return self._stream is not None

    @property
    def dropped_input_blocks(self) -> int:
        return self._stream.dropped_input_blocks if self._stream else 0

    @property
    def starved_output_blocks(self) -> int:
        return self._stream.starved_output_blocks if self._stream else 0

    def start(self) -> None:
        silence = np.zeros((self.block_size, 1), dtype=np.float32)
        for _ in range(PRIME_BLOCKS):
            self._output.put_nowait(silence)
        self._stop.clear()
        self._stream = self._stream_factory(
            sample_rate=self.processor.settings.sample_rate,
            block_size=self.block_size,
            input_queue=self._input,
            output_queue=self._output,
            device=(self._input_device, self._output_device),
        )
        self._threads = [threading.Thread(target=self._work, name="audio-worker", daemon=True)]
        if self._cos_input is not None:
            self._threads.append(threading.Thread(target=self._poll_cos, name="audio-cos", daemon=True))
        for thread in self._threads:
            thread.start()
        self._stream.start()
        _logger.info(
            "audio engine started (in=%s, out=%s, %d Hz)",
            self._input_device or "default", self._output_device or "default", self.processor.settings.sample_rate,
        )

    def stop(self) -> None:
        self._stop.set()
        if self._stream is not None:
            try:
                self._stream.stop()
            finally:
                self._stream = None
        for thread in self._threads:
            thread.join(timeout=1.0)
        self._threads = []
        self._set_transmitting(False)
        _logger.info("audio engine stopped")

    def process_one(self, block: np.ndarray) -> None:
        """One worker step -- separate from the thread loop for testing."""
        result = self.processor.process(block)
        try:
            self._output.put_nowait(result.out.reshape(-1, 1))
        except queue.Full:
            pass  # the callback fell behind; dropping beats unbounded latency
        self._set_transmitting(result.transmitting)
        if result.events:
            self._on_events(result.events)

    def _work(self) -> None:
        while not self._stop.is_set():
            try:
                block = self._input.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                self.process_one(block)
            except Exception:
                _logger.exception("audio processing failed for one block")

    def _poll_cos(self) -> None:
        assert self._cos_input is not None
        while not self._stop.wait(COS_POLL_SECONDS):
            try:
                self.processor.set_external_cos(self._cos_input())
            except OSError:
                _logger.exception("reading hardware COS failed")
                self._stop.wait(1.0)

    def _set_transmitting(self, active: bool) -> None:
        if active == self.transmitting:
            return
        self.transmitting = active
        if self._ptt_output is not None:
            try:
                self._ptt_output(active)
            except OSError:
                _logger.exception("setting hardware PTT failed")
