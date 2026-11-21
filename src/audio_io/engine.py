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
import time
from typing import Callable, Optional

import numpy as np

from controller.events import ControllerEvent
from observability import log_event

from .audio_stream import AudioStream
from .processor import AudioProcessor
from .resample import StreamResampler

_logger = logging.getLogger("moreopenrepeater.audio")

QUEUE_BLOCKS = 10
PRIME_BLOCKS = 2  # output cushion so the first callbacks aren't starved
COS_POLL_SECONDS = 0.02
def _monotonic() -> float:
    """The cap's clock, in one place so tests can move it."""
    return time.monotonic()


# A sound card that drops off USB under a running stream does not always make PortAudio report the stream as
# inactive: the callbacks just stop. The callback only skips a block when the queue is full, so a queue that stays
# empty this long means the card has stopped delivering (blocks come every 20 ms). Well past a busy Pi's worst pause,
# since an empty queue (not a slow worker) is the evidence.
INPUT_SILENCE_SECONDS = 1.5
PTT_RETRY_SECONDS = 0.05
PTT_RETRY_MAX_SECONDS = 1.0
PTT_RELEASE_DELAYS = (0, 0.05, 0.1, 0.2)
# How long past the longest limit the controller is configured to enforce a
# transmission may run before the engine ends it itself. Large on purpose: the
# cap is a backstop for a stuck controller and must never fire during a normal
# time-out, a net, an autopatch call or a long announcement.
TX_LIMIT_MARGIN_SECONDS = 300.0
# The stream's glitch counters, each reported to `on_glitch` as it goes up.
GLITCH_COUNTERS = ("dropped_input_blocks", "starved_output_blocks", "input_overflows", "output_underflows")
# Blocks in a row that the processor may fail on while the transmitter is keyed before the engine unkeys it
# itself (5 blocks is 100 ms of audio). A single bad block is not worth dropping a transmission for, but
# audio that keeps failing under a keyed transmitter is no longer under the controller's control.
KEYED_FAILURE_LIMIT = 5
# The tap's failures are logged on the first and then once in this many, so a broken tap does not log 50 lines a second.
TAP_FAILURE_LOG_EVERY = 500


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
        on_audio: Optional[Callable[[np.ndarray, np.ndarray], None]] = None,
        on_glitch: Optional[Callable[[str, int], None]] = None,
        on_stream_dead: Optional[Callable[[], None]] = None,
        ptt_allowed: Optional[Callable[[], bool]] = None,
        max_transmit_seconds: Optional[float] = None,
        on_transmit_capped: Optional[Callable[[float], None]] = None,
    ) -> None:
        """`max_transmit_seconds`: the longest the transmitter may stay keyed
        without a break, enforced here on the audio worker thread, independent of
        the controller and the event loop. `None` means no limit. When it trips
        the engine unkeys and silences its output until the source stops, then
        allows keying again. `on_transmit_capped(seconds)` runs on the worker
        thread when it trips.

        `on_audio(received, transmitted)` sees every processing block, on
        the worker thread -- it must be quick. `on_glitch(counter, count)`
        too: a GLITCH_COUNTERS name and how much it just went up.
        `on_stream_dead` runs once when the PortAudio stream goes inactive
        (USB unplug / device reset), still on the worker thread."""
        self._on_glitch = on_glitch
        self._on_stream_dead = on_stream_dead
        self._stream_dead_reported = False
        self._glitches_seen = dict.fromkeys(GLITCH_COUNTERS, 0)
        self.processor = processor
        self._on_audio = on_audio
        self.block_size = block_size
        self._on_events = on_events
        self._input_device = input_device
        self._output_device = output_device
        self._ptt_output = ptt_output
        self._ptt_allowed = ptt_allowed
        self._cos_input = cos_input
        self._stream_factory = stream_factory
        self._input: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=QUEUE_BLOCKS)
        self._output: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=QUEUE_BLOCKS)
        self._stream: Optional[AudioStream] = None
        self._threads: list[threading.Thread] = []
        self._stop = threading.Event()
        self._ptt_lock = threading.Lock()
        self._ptt_inhibited = False
        self._ptt_blocked = False
        self._ptt_retry_at = 0.0
        self._ptt_retry_delay = PTT_RETRY_SECONDS
        self.ptt_error: Optional[str] = None
        self.transmitting = False
        self.max_transmit_seconds = max_transmit_seconds
        self._on_transmit_capped = on_transmit_capped
        self._tx_since: Optional[float] = None  # when the current unbroken transmission began
        self.tx_capped = False  # the cap tripped and the source has not stopped yet
        self.tx_cap_trips = 0
        self.last_block_at: Optional[float] = None  # time.monotonic(); the watchdog checks it
        self._wanted = False  # what the processor last asked of the transmitter, for a block it fails to process
        self._failed_blocks = 0  # blocks in a row the processor failed on
        self._failure_release_logged = False
        self._tap_failures = 0
        self._configure_device(processor.settings.sample_rate, block_size)

    def _configure_device(self, device_rate: int, device_block_size: int) -> None:
        rate = self.processor.settings.sample_rate
        self.device_sample_rate = device_rate
        self._device_block_size = device_block_size
        self._rx_resampler = StreamResampler(device_rate, rate)
        self._tx_resampler = StreamResampler(rate, device_rate)
        self._rx_pending = np.zeros(0, dtype=np.float32)
        self._tx_pending = np.zeros(0, dtype=np.float32)

    @property
    def running(self) -> bool:
        stream = self._stream
        return stream is not None and getattr(stream, "active", True)

    @property
    def dropped_input_blocks(self) -> int:
        return self._stream.dropped_input_blocks if self._stream else 0

    @property
    def starved_output_blocks(self) -> int:
        return self._stream.starved_output_blocks if self._stream else 0

    def glitch_counts(self) -> dict[str, int]:
        return {name: getattr(self._stream, name, 0) if self._stream else 0 for name in GLITCH_COUNTERS}

    def check_glitches(self) -> None:
        """Report counters that went up since the last check (worker thread)."""
        for name, count in self.glitch_counts().items():
            new = count - self._glitches_seen[name]
            self._glitches_seen[name] = count
            if new > 0 and self._on_glitch is not None:
                self._on_glitch(name, new)

    def start(self) -> None:
        self._stop.clear()
        self._ptt_inhibited = False
        self._stream_dead_reported = False
        rate = self.processor.settings.sample_rate
        self._stream = self._stream_factory(
            sample_rate=rate,
            block_size=self.block_size,
            input_queue=self._input,
            output_queue=self._output,
            device=(self._input_device, self._output_device),
        )
        self._configure_device(self._stream.sample_rate, self._stream.block_size)
        self._glitches_seen = dict.fromkeys(GLITCH_COUNTERS, 0)
        silence = np.zeros((self._device_block_size, 1), dtype=np.float32)
        for _ in range(PRIME_BLOCKS):
            self._output.put_nowait(silence)
        self.last_block_at = time.monotonic()
        # Start the stream before the worker so `active` is true before the
        # first `_stream_went_away` check (otherwise a fresh start looks dead).
        self._stream.start()
        self._threads = [threading.Thread(target=self._work, name="audio-worker", daemon=True)]
        if self._cos_input is not None:
            self._threads.append(threading.Thread(target=self._poll_cos, name="audio-cos", daemon=True))
        for thread in self._threads:
            thread.start()
        log_event(
            _logger, logging.INFO, "audio.engine_started",
            "audio engine started (in=%s, out=%s, device %d Hz, processing %d Hz)"
            % (self._input_device or "default", self._output_device or "default", self.device_sample_rate, rate),
            device_rate_hz=self.device_sample_rate, processing_rate_hz=rate, facts_in_text=False,
        )

    def stop(self) -> None:
        self._stop.set()
        self.release_ptt(force=False)
        if self._stream is not None:
            try:
                self._stream.stop()
            except Exception:
                _logger.exception("couldn't close the audio stream")
            finally:
                self._stream = None
        current = threading.current_thread()
        for thread in self._threads:
            if thread is not current:
                thread.join(timeout=1.0)
        self._threads = []
        log_event(_logger, logging.INFO, "audio.engine_stopped", "audio engine stopped")

    def process_one(self, block: np.ndarray) -> None:
        """One worker step for one device block -- separate from the thread
        loop for testing. Resampling can make a device block a sample more or
        less than one processing block, so both directions go through FIFOs."""
        rx = self._rx_resampler.process(block)
        self._rx_pending = np.concatenate([self._rx_pending, rx])
        while len(self._rx_pending) >= self.block_size:
            chunk = self._rx_pending[: self.block_size]
            self._rx_pending = self._rx_pending[self.block_size :]
            try:
                result = self.processor.process(chunk)
            except Exception:
                self._processing_failed()
                raise
            self._failed_blocks = 0
            self._failure_release_logged = False
            self._wanted = result.transmitting
            # PTT and the transmit cap come first: nothing the audio does after this can skip them.
            self._set_transmitting(result.transmitting)
            self._tap(chunk, result.out)
            out = np.zeros_like(result.out) if self.tx_capped else result.out  # also stops a VOX radio
            self._tx_pending = np.concatenate([self._tx_pending, self._tx_resampler.process(out)])
            if result.events:
                self._on_events(result.events)
        while len(self._tx_pending) >= self._device_block_size:
            out = self._tx_pending[: self._device_block_size]
            self._tx_pending = self._tx_pending[self._device_block_size :]
            try:
                self._output.put_nowait(out.reshape(-1, 1))
            except queue.Full:
                pass  # the callback fell behind; dropping beats unbounded latency

    def _tap(self, received: np.ndarray, transmitted: np.ndarray) -> None:
        """Show a block to the `on_audio` observer. A broken observer must not touch the block."""
        if self._on_audio is None:
            return
        try:
            self._on_audio(received, transmitted)
        except Exception:
            self._tap_failures += 1
            if self._tap_failures % TAP_FAILURE_LOG_EVERY == 1:
                log_event(
                    _logger, logging.ERROR, "audio.tap_failed", "the audio tap failed; audio carries on without it",
                    error_code="audio.tap", failures=self._tap_failures, exc_info=True, facts_in_text=False,
                )

    def _processing_failed(self) -> None:
        """The processor raised for a block, so there is no new answer about PTT. Keep the transmitter and the
        transmit cap moving anyway: the cap clock still advances on what was last asked, and after
        `KEYED_FAILURE_LIMIT` failed blocks in a row while keyed the engine unkeys the transmitter itself."""
        self._failed_blocks += 1
        wanted = self._wanted
        if wanted and self._failed_blocks >= KEYED_FAILURE_LIMIT:
            wanted = self._wanted = False
            if not self._failure_release_logged:
                self._failure_release_logged = True
                log_event(
                    _logger, logging.CRITICAL, "ptt.released_after_failures",
                    "audio processing failed %d blocks in a row while keyed; unkeying the transmitter" % self._failed_blocks,
                    error_code="audio.process", blocks=self._failed_blocks, facts_in_text=False,
                )
        try:
            self._set_transmitting(wanted)
        except Exception:
            _logger.exception("couldn't update PTT after a failed block")

    def _work(self) -> None:
        while not self._stop.is_set():
            if self._stream_went_away():
                return
            try:
                block = self._input.get(timeout=0.2)
            except queue.Empty:
                if self._input_went_silent():
                    return
                self._retry_ptt()
                continue
            try:
                self.process_one(block)
                self.check_glitches()
            except Exception:
                log_event(
                    _logger, logging.ERROR, "audio.block_failed", "audio processing failed for one block",
                    error_code="audio.process", target="engine", exc_info=True, facts_in_text=False,
                )
            self.last_block_at = time.monotonic()

    def _stream_went_away(self) -> bool:
        stream = self._stream
        if stream is None or getattr(stream, "active", True):
            return False
        return self._report_stream_dead("inactive", "audio stream died (USB unplug or device reset)")

    def _input_went_silent(self) -> bool:
        """The worker's input queue was empty for its whole wait and nothing has arrived for
        `INPUT_SILENCE_SECONDS`: the sound card stopped delivering while the stream still looks active."""
        last = self.last_block_at
        if self._stream is None or last is None or time.monotonic() - last <= INPUT_SILENCE_SECONDS:
            return False
        return self._report_stream_dead(
            "no_audio",
            "no audio from the sound card for %.1f s (USB unplug or device reset)" % INPUT_SILENCE_SECONDS,
        )

    def _report_stream_dead(self, reason: str, message: str) -> bool:
        if self._stream_dead_reported:
            return True
        self._stream_dead_reported = True
        log_event(_logger, logging.ERROR, "audio.stream_died", message, reason=reason, facts_in_text=False)
        callback = self._on_stream_dead
        if callback is not None:
            try:
                callback()
            except Exception:
                _logger.exception("audio stream-death handler failed")
        return True

    def _poll_cos(self) -> None:
        assert self._cos_input is not None
        while not self._stop.wait(COS_POLL_SECONDS):
            if self._stream_went_away():
                return
            try:
                self.processor.set_external_cos(self._cos_input())
            except OSError:
                log_event(
                    _logger, logging.ERROR, "cos.read_failed", "reading hardware COS failed",
                    error_code="cos.hardware", target="engine", exc_info=True, facts_in_text=False,
                )
                self._stop.wait(1.0)

    def _write_ptt(self, active: bool) -> bool:
        """Record the last successful write. Caller holds the PTT lock."""
        try:
            if self._ptt_output is not None:
                self._ptt_output(active)
        except OSError as error:
            action = "key" if active else "release"
            message = f"Couldn't {action} hardware PTT: {error}"
            if message != self.ptt_error:
                log_event(
                    _logger, logging.ERROR, "ptt.failed", message, error_code="ptt.hardware", action=action, facts_in_text=False,
                )
            self.ptt_error = message
            self._ptt_blocked |= active
            self._ptt_retry_at = time.monotonic() + self._ptt_retry_delay
            self._ptt_retry_delay = min(2 * self._ptt_retry_delay, PTT_RETRY_MAX_SECONDS)
            return False
        if active != self.transmitting:
            log_event(
                _logger, logging.INFO, "ptt.applied", f"transmitter {'keyed' if active else 'unkeyed'} at the hardware",
                active=active, facts_in_text=False,
            )
        self.transmitting = active
        self.ptt_error = None
        self._ptt_retry_delay = PTT_RETRY_SECONDS
        return True

    def _retry_ptt(self) -> None:
        with self._ptt_lock:
            if self.ptt_error is not None and not self._ptt_inhibited and time.monotonic() >= self._ptt_retry_at:
                self._write_ptt(False)

    def set_max_transmit_seconds(self, seconds: Optional[float]) -> None:
        """Change the limit while running (the controller's own limits changed)."""
        self.max_transmit_seconds = seconds

    def _apply_transmit_cap(self, wanted: bool) -> bool:
        """What the transmitter should do after the cap. Caller holds the PTT lock."""
        if not wanted:
            self._tx_since = None
            self.tx_capped = False  # the source stopped; the next transmission may key
            return False
        now = _monotonic()
        if self._tx_since is None:
            self._tx_since = now
        limit = self.max_transmit_seconds
        if limit is not None and not self.tx_capped and now - self._tx_since > limit:
            self.tx_capped = True
            self.tx_cap_trips += 1
            log_event(
                _logger, logging.CRITICAL, "ptt.transmit_capped",
                "transmitter was keyed for %.0f s without a break, past the %.0f s limit; unkeying until the source stops"
                % (now - self._tx_since, limit),
                error_code="ptt.cap", keyed_s=round(now - self._tx_since), limit_s=round(limit), facts_in_text=False,
            )
            callback = self._on_transmit_capped
            if callback is not None:
                try:
                    callback(now - self._tx_since)
                except Exception:
                    _logger.exception("transmit-cap handler failed")
        return not self.tx_capped

    def _set_transmitting(self, active: bool) -> None:
        with self._ptt_lock:
            if self._ptt_inhibited:
                return
            if active and self._ptt_allowed is not None and not self._ptt_allowed():
                active = False
            active = self._apply_transmit_cap(active)
            if not active:
                self._ptt_blocked = False
            if self.ptt_error is not None:
                self._ptt_blocked |= active
                if time.monotonic() >= self._ptt_retry_at:
                    self._write_ptt(False)
            elif active != self.transmitting and not self._ptt_blocked:
                self._write_ptt(active)

    def confirm_ptt_idle(self) -> bool:
        """Confirm an idle output before starting a replacement audio stream."""
        with self._ptt_lock:
            return self._write_ptt(False)

    def release_ptt(self, *, force: bool = True) -> None:
        """Inhibit further keying and try to release PTT before hardware closes."""
        with self._ptt_lock:
            self._ptt_inhibited = True
            if not (force or self.transmitting or self.ptt_error is not None):
                return
            for delay in PTT_RELEASE_DELAYS:
                if delay:
                    time.sleep(delay)
                if self._write_ptt(False):
                    return
            log_event(
                _logger, logging.CRITICAL, "ptt.release_unconfirmed",
                f"hardware PTT release could not be confirmed: {self.ptt_error}", error_code="ptt.hardware", facts_in_text=False,
            )
