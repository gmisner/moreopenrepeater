"""Runs the monitor receiver (`audio_io.receiver`) from the dashboard settings.

Its audio goes to its own `AudioMonitor` (the dashboard's "Monitor" player)
and, when turned on, to its own recordings -- never to the repeater's engine,
the public listening page or Broadcastify.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable, Optional

import numpy as np

from audio_io.pi_gpio import GpioLine, open_header_pin
from audio_io.receiver import MonitorReceiver, MonitorSettings
from controller.state_machine import RepeaterConfig

from .monitor import AudioMonitor
from .recordings import RecordingStore
from .service import RepeaterService

_logger = logging.getLogger("moreopenrepeater.monitor_receiver")

BLOCK_SECONDS = 0.02
MIN_RECORDING_SECONDS = 1.0
MAX_RECORDING_SECONDS = 300.0
_RESTART_FIELDS = (
    "monitor_enabled", "monitor_input_device", "monitor_squelch", "monitor_gpio_pin", "monitor_gpio_polarity",
    "audio_enabled", "audio_input_device", "cos_source", "cos_gpio_pin", "ptt_output", "ptt_gpio_pin",
)


def setup_problem(config: RepeaterConfig) -> Optional[str]:
    """Why these settings can't run the monitor, or None."""
    if config.audio_enabled and config.monitor_input_device == config.audio_input_device:
        return "The monitor needs its own input device, not the repeater's receiver."
    if config.monitor_squelch == "gpio":
        if config.audio_enabled and config.cos_source == "gpio" and config.cos_gpio_pin == config.monitor_gpio_pin:
            return f"GPIO{config.monitor_gpio_pin} is already the repeater's COS pin."
        if config.audio_enabled and config.ptt_output == "gpio" and config.ptt_gpio_pin == config.monitor_gpio_pin:
            return f"GPIO{config.monitor_gpio_pin} is already the repeater's PTT pin."
    return None


class MonitorReceiverService:
    def __init__(
        self,
        service: RepeaterService,
        sample_rate: int,
        recordings: Optional[RecordingStore] = None,
        receiver_factory: Callable[..., MonitorReceiver] = MonitorReceiver,
        open_pin: Callable[..., GpioLine] = open_header_pin,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._service = service
        self._sample_rate = sample_rate
        self._recordings = recordings
        self._receiver_factory = receiver_factory
        self._open_pin = open_pin
        self._clock = clock
        self._pin: Optional[GpioLine] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._applied: Optional[RepeaterConfig] = None
        self.receiver: Optional[MonitorReceiver] = None
        self.error: Optional[str] = None
        self.monitor = AudioMonitor()
        self._capture: Optional[list[np.ndarray]] = None
        self._capture_started_at = 0.0

    def attach(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._service.add_config_listener(self.apply_config)
        self.apply_config(self._service.config)

    def shutdown(self) -> None:
        self._stop()

    def apply_config(self, config: RepeaterConfig) -> None:
        previous, self._applied = self._applied, config
        if previous is None or any(getattr(previous, f) != getattr(config, f) for f in _RESTART_FIELDS):
            self._stop()
            if config.monitor_enabled:
                self._start(config)
        elif self.receiver is not None:
            self.receiver.settings.vox_threshold_db = config.monitor_vox_threshold_db
            self.receiver.settings.gain_db = config.monitor_gain_db

    def status(self) -> dict:
        config = self._service.config
        receiver = self.receiver
        return {
            "enabled": config.monitor_enabled,
            "running": receiver is not None and receiver.running,
            "error": self.error,
            "name": config.monitor_name,
            "level_db": receiver.level_db if receiver else -120.0,
            "squelch_open": receiver.squelch_open if receiver else False,
            "device_sample_rate": receiver.device_sample_rate if receiver else None,
            "sample_rate": self._sample_rate,
            "dropped_input_blocks": receiver.dropped_input_blocks if receiver else 0,
            "listeners": self.monitor.listener_count,
        }

    # -- worker thread --------------------------------------------------------

    def _on_audio(self, block: np.ndarray, squelch_open: bool) -> None:
        self.monitor.feed(block, block)
        capture = self._capture
        if capture is not None and squelch_open and len(capture) * len(block) < MAX_RECORDING_SECONDS * self._sample_rate:
            capture.append(block.copy())

    def _on_squelch(self, open_: bool) -> None:
        if open_:
            if self._recording_wanted():
                self._capture, self._capture_started_at = [], self._clock()
            return
        captured, self._capture = self._capture, None
        if not captured or self._loop is None:
            return
        samples = np.concatenate(captured)
        if len(samples) >= MIN_RECORDING_SECONDS * self._sample_rate:
            self._loop.call_soon_threadsafe(self._loop.run_in_executor, None, self._save, samples, self._capture_started_at)

    def _recording_wanted(self) -> bool:
        return self._service.config.monitor_record and self._recordings is not None and self._recordings.enabled

    def _save(self, samples: np.ndarray, started_at: float) -> None:
        try:
            self._recordings.save(samples, started_at)
            self._recordings.prune_days(self._service.config.recording_retention_days)
        except OSError:
            _logger.exception("couldn't save a monitor recording")

    # -- lifecycle -------------------------------------------------------------

    def _start(self, config: RepeaterConfig) -> None:
        problem = setup_problem(config)
        if problem:
            self.error = problem
            return
        cos_input = None
        if config.monitor_squelch == "gpio":
            try:
                self._pin = self._open_pin(config.monitor_gpio_pin, output=False, active_low=config.monitor_gpio_polarity == "low")
            except OSError as error:
                self.error = f"couldn't open GPIO{config.monitor_gpio_pin} for the monitor's squelch: {error.strerror or error}"
                _logger.error("%s", self.error)
                return
            cos_input = self._pin.read
        receiver = self._receiver_factory(
            MonitorSettings(
                sample_rate=self._sample_rate, squelch=config.monitor_squelch,
                vox_threshold_db=config.monitor_vox_threshold_db, gain_db=config.monitor_gain_db,
            ),
            int(self._sample_rate * BLOCK_SECONDS),
            on_audio=self._on_audio,
            on_squelch=self._on_squelch,
            input_device=config.monitor_input_device or None,
            cos_input=cos_input,
        )
        try:
            receiver.start()
        except Exception as error:  # PortAudio raises its own error types
            self.error = f"couldn't open the monitor's input: {error}"
            _logger.error("%s", self.error)
            try:
                receiver.stop()
            except Exception:
                pass
            self._release_pin()
            return
        self.receiver = receiver
        self.error = None

    def _stop(self) -> None:
        if self.receiver is not None:
            self.receiver.stop()
            self.receiver = None
        self._release_pin()
        self._capture = None
        self.error = None

    def _release_pin(self) -> None:
        if self._pin is not None:
            try:
                self._pin.close()
            except OSError:
                pass
            self._pin = None
