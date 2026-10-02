"""A listen-only second input: a monitor receiver (an aviation frequency, a
weather channel, the repeater's own output from across town).

Receive-only by construction, because retransmitting other services isn't
allowed (47 CFR 97.113): it opens a PortAudio *input* stream with no output
side, and hands its audio only to the callbacks it's given. It never touches an
`AudioProcessor` (whose output is the only audio that reaches the transmitter)
or the controller, so its squelch can't key the repeater either.
"""
from __future__ import annotations

import logging
import math
import queue
import threading
import time
from dataclasses import dataclass
from typing import Callable, Literal, Optional

import numpy as np

from .audio_stream import sd
from .resample import StreamResampler

_logger = logging.getLogger("moreopenrepeater.monitor_receiver")

MonitorSquelch = Literal["vox", "gpio", "open"]
QUEUE_BLOCKS = 10
COS_POLL_SECONDS = 0.02
SILENCE_DB = -120.0
_HYSTERESIS_DB = 3.0
_FALLBACK_RATES = (48000, 44100)


def level_db(block: np.ndarray) -> float:
    rms = float(np.sqrt(np.mean(np.square(block, dtype=np.float64)))) if len(block) else 0.0
    return 20 * math.log10(rms) if rms > 1e-6 else SILENCE_DB


class InputBlockPump:
    """The PortAudio callback: only a non-blocking queue put."""

    def __init__(self, input_queue: "queue.Queue[np.ndarray]") -> None:
        self.input_queue = input_queue
        self.dropped_input_blocks = 0

    def process(self, indata: np.ndarray, frames: int, time, status) -> None:
        try:
            self.input_queue.put_nowait(indata.copy())
        except queue.Full:
            self.dropped_input_blocks += 1


class InputStream:
    """A PortAudio input stream, at `sample_rate` if the device takes it,
    otherwise its own rate (exposed as `sample_rate`, with `block_size` the
    same duration) for the caller to resample."""

    def __init__(self, sample_rate: int, block_size: int, input_queue: "queue.Queue[np.ndarray]", device=None) -> None:
        if sd is None:
            raise RuntimeError("sounddevice/PortAudio is not available on this system")
        self.sample_rate = self._pick_rate(device, sample_rate)
        self.block_size = round(block_size * self.sample_rate / sample_rate)
        self._pump = InputBlockPump(input_queue)
        self._stream = sd.InputStream(
            samplerate=self.sample_rate, blocksize=self.block_size, channels=1, dtype="float32",
            device=device, callback=self._pump.process,
        )

    @staticmethod
    def _pick_rate(device, preferred: int) -> int:
        candidates = [preferred]
        try:
            candidates.append(int(sd.query_devices(device, "input")["default_samplerate"]))
        except (ValueError, sd.PortAudioError):
            pass
        for rate in dict.fromkeys(candidates + list(_FALLBACK_RATES)):
            try:
                sd.check_input_settings(device, channels=1, dtype="float32", samplerate=rate)
            except (ValueError, sd.PortAudioError):
                continue
            return rate
        raise RuntimeError(f"no sample rate works for the input (tried {list(dict.fromkeys(candidates))})")

    def start(self) -> None:
        self._stream.start()

    def stop(self) -> None:
        self._stream.stop()
        self._stream.close()

    @property
    def dropped_input_blocks(self) -> int:
        return self._pump.dropped_input_blocks


@dataclass
class MonitorSettings:
    sample_rate: int
    squelch: MonitorSquelch = "vox"
    vox_threshold_db: float = -40.0
    vox_attack: float = 0.06
    vox_hold: float = 0.8  # longer than the repeater's: other services pause more between words
    gain_db: float = 0.0


class MonitorReceiver:
    """`on_audio(block, squelch_open)` gets every processing block (silence
    while the squelch is closed) and `on_squelch(open)` each change, both on
    the worker thread -- they must be quick."""

    def __init__(
        self,
        settings: MonitorSettings,
        block_size: int,
        on_audio: Callable[[np.ndarray, bool], None],
        on_squelch: Callable[[bool], None] = lambda open_: None,
        input_device: Optional[str] = None,
        cos_input: Optional[Callable[[], bool]] = None,
        stream_factory: Callable[..., InputStream] = InputStream,
    ) -> None:
        self.settings = settings
        self.block_size = block_size
        self._on_audio = on_audio
        self._on_squelch = on_squelch
        self._input_device = input_device
        self._cos_input = cos_input
        self._stream_factory = stream_factory
        self._input: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=QUEUE_BLOCKS)
        self._stream: Optional[InputStream] = None
        self._threads: list[threading.Thread] = []
        self._stop = threading.Event()
        self.level_db = SILENCE_DB
        self.squelch_open = False
        self._external_cos = False
        self._above = 0.0
        self._below = 0.0
        self.last_block_at: Optional[float] = None
        self._configure_device(settings.sample_rate)

    def _configure_device(self, device_rate: int) -> None:
        self.device_sample_rate = device_rate
        self._resampler = StreamResampler(device_rate, self.settings.sample_rate)
        self._pending = np.zeros(0, dtype=np.float32)

    @property
    def running(self) -> bool:
        return self._stream is not None

    @property
    def dropped_input_blocks(self) -> int:
        return self._stream.dropped_input_blocks if self._stream else 0

    def start(self) -> None:
        self._stop.clear()
        self._stream = self._stream_factory(
            sample_rate=self.settings.sample_rate, block_size=self.block_size,
            input_queue=self._input, device=self._input_device,
        )
        self._configure_device(self._stream.sample_rate)
        self.last_block_at = time.monotonic()
        self._threads = [threading.Thread(target=self._work, name="monitor-worker", daemon=True)]
        if self._cos_input is not None:
            self._threads.append(threading.Thread(target=self._poll_cos, name="monitor-cos", daemon=True))
        for thread in self._threads:
            thread.start()
        self._stream.start()
        _logger.info("monitor receiver started (in=%s, device %d Hz)", self._input_device or "default", self.device_sample_rate)

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
        if self.squelch_open:
            self.squelch_open = False
            self._on_squelch(False)
        _logger.info("monitor receiver stopped")

    def set_external_cos(self, active: bool) -> None:
        self._external_cos = active

    def process_one(self, block: np.ndarray) -> None:
        """One worker step for one device block (separate for testing)."""
        self._pending = np.concatenate([self._pending, self._resampler.process(np.asarray(block, dtype=np.float32).reshape(-1))])
        while len(self._pending) >= self.block_size:
            chunk = self._pending[: self.block_size]
            self._pending = self._pending[self.block_size :]
            self.level_db = level_db(chunk)
            squelch = self._squelch(len(chunk))
            if squelch != self.squelch_open:
                self.squelch_open = squelch
                self._on_squelch(squelch)
            if squelch:
                out = np.clip(chunk * (10 ** (self.settings.gain_db / 20)), -1.0, 1.0).astype(np.float32)
            else:
                out = np.zeros_like(chunk)
            self._on_audio(out, squelch)

    def _squelch(self, n: int) -> bool:
        squelch = self.settings.squelch
        if squelch == "open":
            return True
        if squelch == "gpio":
            return self._external_cos
        seconds = n / self.settings.sample_rate
        threshold = self.settings.vox_threshold_db - (_HYSTERESIS_DB if self.squelch_open else 0.0)
        if self.level_db >= threshold:
            self._above += seconds
            self._below = 0.0
        else:
            self._below += seconds
            self._above = 0.0
        if not self.squelch_open:
            return self._above >= self.settings.vox_attack - 1e-9
        return self._below < self.settings.vox_hold - 1e-9

    def _work(self) -> None:
        while not self._stop.is_set():
            try:
                block = self._input.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                self.process_one(block)
            except Exception:
                _logger.exception("monitor processing failed for one block")
            self.last_block_at = time.monotonic()

    def _poll_cos(self) -> None:
        assert self._cos_input is not None
        while not self._stop.wait(COS_POLL_SECONDS):
            try:
                self.set_external_cos(self._cos_input())
            except OSError:
                _logger.exception("reading the monitor's squelch pin failed")
                self._stop.wait(1.0)
