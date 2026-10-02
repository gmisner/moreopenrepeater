"""Connects the live audio engine to the repeater service.

  - Starts, stops and restarts the `AudioEngine` as the dashboard settings
    change (device or COS-source changes restart it; level/gain settings
    apply live).
  - Implements the service's `AudioOutput`: PTT, "repeat receive audio",
    and clip playback (from the renderer's cache when possible, otherwise
    rendered off the event loop first).
  - Forwards detected carrier / CTCSS / DTMF back to the service on the
    event loop thread.
  - Records repeated transmissions (when turned on), and runs parrot: once
    armed, the next transmission isn't repeated live but saved and played
    back after the user unkeys.

A CM108 USB interface, found at startup (Linux), keys the radio's PTT and
can supply COS. Its
spare pins are `api.gpio`'s. On a Raspberry Pi, PTT and COS can use header
pins instead, held only while the engine runs.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable, Optional

import numpy as np

from audio_io.audio_stream import sd
from audio_io.cm108 import CM108Interface, LinuxHidrawDevice, find_interfaces
from audio_io.engine import AudioEngine
from audio_io.patch import LinkAudio, PatchAudio
from audio_io.pi_gpio import GpioLine, open_header_pin
from audio_io.processor import AudioProcessor, ProcessorSettings
from controller.events import COSChanged, CTCSSChanged
from controller.state_machine import RepeaterConfig
from playout.renderer import RECORDING_PREFIX, ClipRenderer, UnknownClipError
from playout.tts import TTSError

from .monitor import AudioMonitor
from .recordings import RecordingStore
from .service import RepeaterService

_logger = logging.getLogger("moreopenrepeater.audio")

BLOCK_SECONDS = 0.02
MIN_RECORDING_SECONDS = 1.0  # shorter captures are kerchunks, not worth keeping
MAX_RECORDING_SECONDS = 300.0
MAX_PARROT_SECONDS = 30.0
PARROT_ARMED_SECONDS = 60.0  # give up waiting for the parrot transmission after this
_RESTART_FIELDS = (
    "audio_enabled",
    "audio_input_device",
    "audio_output_device",
    "cos_source",
    "cos_polarity",
    "cos_gpio_pin",
    "ptt_output",
    "ptt_gpio_pin",
    "ptt_polarity",
)
# Same names on RepeaterConfig and ProcessorSettings; applied without a restart.
_LIVE_FIELDS = ("vox_threshold_db", "vox_hold", "tx_gain_db", "tx_ctcss_hz", "tx_ctcss_level_db")


def list_audio_devices() -> list[dict]:
    if sd is None:
        return []
    return [
        {
            "name": d["name"],
            "inputs": d["max_input_channels"],
            "outputs": d["max_output_channels"],
            "default_samplerate": d["default_samplerate"],
        }
        for d in sd.query_devices()
    ]


def cm108_from_env(env: dict, find: Callable[[], list[dict]] = find_interfaces) -> Optional[CM108Interface]:
    """`MOREOPENREPEATER_CM108_HIDRAW`: a /dev/hidrawN path, "off", or
    "auto" (the default) for the first CM108 plugged in at startup."""
    setting = env.get("MOREOPENREPEATER_CM108_HIDRAW", "").strip() or "auto"
    if setting == "off":
        return None
    if setting != "auto":
        return _unkeyed(CM108Interface(LinuxHidrawDevice(setting)))
    found = find()
    if not found:
        return None
    path = found[0]["path"]
    try:
        device = LinuxHidrawDevice(path)
    except OSError as error:
        _logger.error("found a CM108 interface at %s but couldn't open it: %s", path, error)
        return None
    _logger.info("using the CM108 interface at %s (%s)", path, found[0]["name"])
    return _unkeyed(CM108Interface(device))


def link_cm108_from_env(
    env: dict, repeater: Optional[CM108Interface], find: Callable[[], list[dict]] = find_interfaces
) -> Optional[CM108Interface]:
    """The link radio's CM108 (api.link_radio). `MOREOPENREPEATER_LINK_CM108_HIDRAW`:
    a /dev/hidrawN path, "off", or "auto" (the default) for the first one
    plugged in that the repeater isn't using."""
    setting = env.get("MOREOPENREPEATER_LINK_CM108_HIDRAW", "").strip() or "auto"
    if setting == "off":
        return None
    if setting == "auto":
        taken = getattr(repeater.device, "path", None) if repeater is not None else None
        spare = [found for found in find() if found["path"] != taken]
        if not spare:
            return None
        setting = spare[0]["path"]
    try:
        device = LinuxHidrawDevice(setting)
    except OSError as error:
        _logger.error("couldn't open the link radio's CM108 at %s: %s", setting, error)
        return None
    _logger.info("using the CM108 interface at %s for the link radio", setting)
    return _unkeyed(CM108Interface(device))


def _unkeyed(interface: CM108Interface) -> CM108Interface:
    """A CM108 keeps its outputs when the process holding it dies, so a
    crash or watchdog restart mid-transmission can leave the radio keyed."""
    try:
        interface.set_ptt(False)
    except OSError as error:
        _logger.error("couldn't unkey the CM108's PTT: %s", error)
    return interface


class LiveAudio:
    def __init__(
        self,
        service: RepeaterService,
        renderer: ClipRenderer,
        cm108: Optional[CM108Interface] = None,
        engine_factory: Callable[..., AudioEngine] = AudioEngine,
        recordings: Optional[RecordingStore] = None,
        clock: Callable[[], float] = time.time,
        open_pin: Callable[..., GpioLine] = open_header_pin,
    ) -> None:
        self._service = service
        self._renderer = renderer
        self._cm108 = cm108
        self._engine_factory = engine_factory
        self._open_pin = open_pin
        self._pins: list[GpioLine] = []
        self._recordings = recordings
        self._clock = clock
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._applied: Optional[RepeaterConfig] = None
        self.engine: Optional[AudioEngine] = None
        self.error: Optional[str] = None
        self._parrot_armed_at: Optional[float] = None
        self._parrot_recording = False
        # The next transmission recorded instead of repeated, for someone else (api.mailbox).
        self._capture_armed: Optional[tuple[float, float, Callable[[np.ndarray, float], None]]] = None
        self._capture_callback: Optional[Callable[[np.ndarray, float], None]] = None
        self._capture_started_at: Optional[float] = None
        self._patch: Optional[PatchAudio] = None
        self._link: Optional[LinkAudio] = None
        self._port: Optional[LinkAudio] = None
        self._ptt_output: Optional[Callable[[bool], None]] = None
        self.monitor = AudioMonitor()

    @property
    def cm108(self) -> Optional[CM108Interface]:
        return self._cm108

    @property
    def hardware_ptt(self) -> Optional[str]:
        """What keys the transmitter: "cm108", "gpio", or None (nothing)."""
        if self._service.config.ptt_output == "gpio":
            return "gpio"
        return "cm108" if self._cm108 is not None else None

    def attach(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._service.audio_output = self
        self._service.add_config_listener(self.apply_config)
        self.apply_config(self._service.config)

    def shutdown(self) -> None:
        self._stop_engine()

    def progress(self) -> Optional[float]:
        """When the engine last handled a block (time.monotonic()), or None while it isn't running."""
        engine = self.engine
        return engine.last_block_at if engine is not None and engine.running else None

    def release_ptt(self) -> None:
        """Unkey the transmitter directly, from any thread, without the
        engine: for when the service is about to be killed."""
        if self._ptt_output is not None:
            self._ptt_output(False)

    def apply_config(self, config: RepeaterConfig) -> None:
        previous = self._applied
        self._applied = config
        needs_restart = previous is None or any(getattr(previous, f) != getattr(config, f) for f in _RESTART_FIELDS)
        if needs_restart:
            self._stop_engine()
            if config.audio_enabled:
                self._start_engine(config)
        elif self.engine is not None:
            settings = self.engine.processor.settings
            for field in _LIVE_FIELDS:
                setattr(settings, field, getattr(config, field))

    def status(self) -> dict:
        config = self._service.config
        engine = self.engine
        processor = engine.processor if engine else None
        return {
            "enabled": config.audio_enabled,
            "running": engine is not None and engine.running,
            "error": self.error,
            "input_device": config.audio_input_device,
            "output_device": config.audio_output_device,
            "sample_rate": self._renderer.sample_rate,
            "device_sample_rate": engine.device_sample_rate if engine else None,
            "rx_level_db": processor.rx_level_db if processor else -120.0,
            "cos_open": processor.cos_open if processor else False,
            "ctcss_hz": processor.ctcss_hz if processor else None,
            "transmitting": engine.transmitting if engine else False,
            "dropped_input_blocks": engine.dropped_input_blocks if engine else 0,
            "starved_output_blocks": engine.starved_output_blocks if engine else 0,
            "hardware_ptt": self.hardware_ptt,
            "listeners": self.monitor.listener_count,
        }

    # -- AudioOutput --------------------------------------------------------

    def set_ptt(self, active: bool) -> None:
        if self.engine is not None:
            self.engine.processor.set_ptt(active)

    def set_repeating(self, repeating: bool) -> None:
        engine = self.engine
        if engine is None:
            return
        processor = engine.processor
        now = self._clock()
        if repeating:
            armed_at = self._parrot_armed_at
            self._parrot_armed_at = None
            self._parrot_recording = armed_at is not None and now - armed_at < PARROT_ARMED_SECONDS
            capture, self._capture_armed = self._capture_armed, None
            if self._parrot_recording:
                processor.start_capture(MAX_PARROT_SECONDS)
                self._capture_started_at = now
            elif capture is not None and now - capture[0] < PARROT_ARMED_SECONDS:
                processor.start_capture(capture[1])
                self._capture_callback = capture[2]
                self._capture_started_at = now
            else:
                processor.set_repeating(True)
                if self._recording_wanted():
                    processor.start_capture(MAX_RECORDING_SECONDS)
                    self._capture_started_at = now
            return
        processor.set_repeating(False)
        if self._capture_started_at is None:
            return
        started_at, self._capture_started_at = self._capture_started_at, None
        parrot, self._parrot_recording = self._parrot_recording, False
        callback, self._capture_callback = self._capture_callback, None
        samples = processor.stop_capture()
        if len(samples) < MIN_RECORDING_SECONDS * self._renderer.sample_rate:
            return
        if self._loop is None:
            return
        if callback is not None:
            self._loop.run_in_executor(None, callback, samples, started_at)
        else:
            self._loop.run_in_executor(None, self._save_recording, samples, started_at, parrot)

    def set_patch(self, patch: Optional[PatchAudio]) -> None:
        self._patch = patch
        if self.engine is not None:
            self.engine.processor.set_patch(patch)

    def set_link(self, link: Optional[LinkAudio]) -> None:
        self._link = link
        if self.engine is not None:
            self.engine.processor.set_link(link)

    def set_port(self, port: Optional[LinkAudio]) -> None:
        self._port = port
        if self.engine is not None:
            self.engine.processor.set_port(port)

    @property
    def repeating_voice(self) -> bool:
        engine = self.engine
        return engine is not None and engine.processor.repeating_voice

    def arm_parrot(self) -> bool:
        if self.engine is None or self._recordings is None or not self._recordings.enabled:
            _logger.warning("parrot needs live audio and a recordings directory")
            return False
        self._parrot_armed_at = self._clock()
        return True

    def arm_capture(self, max_seconds: float, on_done: Callable[[np.ndarray, float], None]) -> bool:
        """Records the next transmission (instead of repeating it) and hands
        it to `on_done(samples, started_at)` on a worker thread."""
        if self.engine is None:
            return False
        self._capture_armed = (self._clock(), max_seconds, on_done)
        return True

    def play(self, clip: str) -> None:
        engine = self.engine
        if engine is None:
            return
        config = self._service.config
        samples = self._renderer.cached_samples(clip, config)
        if samples is not None:
            engine.processor.play(samples)
        elif self._loop is not None:
            self._loop.run_in_executor(None, self._render_and_play, engine, clip, config)

    # -- internals ----------------------------------------------------------

    def _recording_wanted(self) -> bool:
        return (
            self._service.config.record_transmissions
            and self._recordings is not None
            and self._recordings.enabled
        )

    def _save_recording(self, samples: np.ndarray, started_at: float, parrot: bool) -> None:
        try:
            info = self._recordings.save(samples, started_at)
            self._recordings.prune_days(self._service.config.recording_retention_days)
        except OSError:
            _logger.exception("couldn't save recording")
            return
        if parrot and info is not None and self._loop is not None:
            self._loop.call_soon_threadsafe(self._service.speak, RECORDING_PREFIX + info.id)

    def _render_and_play(self, engine: AudioEngine, clip: str, config: RepeaterConfig) -> None:
        try:
            engine.processor.play(self._renderer.render(clip, config))
        except (UnknownClipError, TTSError):
            _logger.exception("couldn't render %s for transmission", clip)

    def _on_events(self, events) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._service.handle_audio_events, events)

    def _start_engine(self, config: RepeaterConfig) -> None:
        cos_source = config.cos_source
        if cos_source == "cm108" and self._cm108 is None:
            self.error = "COS source is CM108, but no CM108 interface was found when the service started"
            return
        gpio_ptt = config.ptt_output == "gpio"
        if gpio_ptt and cos_source == "gpio" and config.ptt_gpio_pin == config.cos_gpio_pin:
            self.error = f"PTT and COS can't both use GPIO{config.ptt_gpio_pin}"
            return
        ptt_output = self._cm108.set_ptt if self._cm108 and not gpio_ptt else None
        cos_input = self._cm108.read_cos if self._cm108 and cos_source == "cm108" else None
        if self._cm108 is not None:
            self._cm108.cos_active_low = config.cos_polarity == "low"
        try:
            if gpio_ptt:
                ptt_output = self._claim_pin(config.ptt_gpio_pin, "PTT", output=True, active_low=config.ptt_polarity == "low").write
            if cos_source == "gpio":
                cos_input = self._claim_pin(config.cos_gpio_pin, "COS", output=False, active_low=config.cos_polarity == "low").read
        except OSError as error:
            self._release_pins()
            self.error = str(error)
            _logger.error("%s", self.error)
            return
        rate = self._renderer.sample_rate
        processor = AudioProcessor(
            ProcessorSettings(
                sample_rate=rate,
                cos_source="external" if cos_source in ("cm108", "gpio") else cos_source,
                **{field: getattr(config, field) for field in _LIVE_FIELDS},
            )
        )
        processor.set_ptt(self._service.ptt_active)
        processor.set_repeating(self._service.repeating)
        processor.set_patch(self._patch)
        processor.set_link(self._link)
        processor.set_port(self._port)
        engine = self._engine_factory(
            processor,
            int(rate * BLOCK_SECONDS),
            on_events=self._on_events,
            input_device=config.audio_input_device or None,
            output_device=config.audio_output_device or None,
            ptt_output=ptt_output,
            cos_input=cos_input,
            on_audio=self.monitor.feed,
        )
        self._ptt_output = ptt_output
        try:
            engine.start()
        except Exception as error:  # PortAudio raises its own error types
            self.error = f"couldn't open audio devices: {error}"
            _logger.error("%s", self.error)
            try:
                engine.stop()
            except Exception:
                pass
            self._ptt_output = None
            self._release_pins()
            return
        self.engine = engine
        self.error = None

    def _claim_pin(self, pin: int, role: str, *, output: bool, active_low: bool) -> GpioLine:
        try:
            line = self._open_pin(pin, output=output, active_low=active_low)
        except OSError as error:
            raise OSError(f"couldn't open GPIO{pin} for {role}: {error.strerror or error}") from error
        self._pins.append(line)
        return line

    def _release_pins(self) -> None:
        for line in self._pins:
            try:
                line.close()
            except OSError:
                pass
        self._pins = []

    def _stop_engine(self) -> None:
        self._ptt_output = None
        if self.engine is not None:
            processor = self.engine.processor
            self.engine.stop()
            self.engine = None
            self._release_pins()
            # Otherwise the controller would sit in RECEIVING until the timeout timer.
            released = []
            if processor.cos_open:
                released.append(COSChanged(active=False))
            if processor.ctcss_hz is not None:
                released.append(CTCSSChanged(tone_hz=None))
            if released:
                self._service.handle_audio_events(released)
        self._capture_started_at = None
        self._parrot_recording = False
        self._capture_armed = None
        self._capture_callback = None
        self.error = None
