"""Connects the live audio engine to the repeater service.

  - Starts, stops and restarts the `AudioEngine` as the dashboard settings
    change (device or COS-source changes restart it; level/gain settings
    apply live).
  - Implements the service's `AudioOutput`: PTT, "repeat receive audio",
    and clip playback (from the renderer's cache when possible, otherwise
    rendered off the event loop first).
  - Forwards detected carrier / CTCSS / DTMF back to the service on the
    event loop thread.

A CM108 USB interface, if `MOREOPENREPEATER_CM108_HIDRAW` names its
/dev/hidrawN node (Linux), keys the radio's PTT and can supply COS.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Callable, Optional

from audio_io.audio_stream import sd
from audio_io.cm108 import CM108Interface, LinuxHidrawDevice
from audio_io.engine import AudioEngine
from audio_io.processor import AudioProcessor, ProcessorSettings
from controller.events import COSChanged, CTCSSChanged
from controller.state_machine import RECEIVING, RepeaterConfig
from playout.renderer import ClipRenderer, UnknownClipError
from playout.tts import TTSError

from .service import RepeaterService

_logger = logging.getLogger("moreopenrepeater.audio")

BLOCK_SECONDS = 0.02
_RESTART_FIELDS = ("audio_enabled", "audio_input_device", "audio_output_device", "cos_source")
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


def cm108_from_env(env: dict) -> Optional[CM108Interface]:
    path = env.get("MOREOPENREPEATER_CM108_HIDRAW")
    return CM108Interface(LinuxHidrawDevice(path)) if path else None


class LiveAudio:
    def __init__(
        self,
        service: RepeaterService,
        renderer: ClipRenderer,
        cm108: Optional[CM108Interface] = None,
        engine_factory: Callable[..., AudioEngine] = AudioEngine,
    ) -> None:
        self._service = service
        self._renderer = renderer
        self._cm108 = cm108
        self._engine_factory = engine_factory
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._applied: Optional[RepeaterConfig] = None
        self.engine: Optional[AudioEngine] = None
        self.error: Optional[str] = None

    @property
    def hardware_ptt(self) -> bool:
        return self._cm108 is not None

    def attach(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._service.audio_output = self
        self._service.add_config_listener(self.apply_config)
        self.apply_config(self._service.config)

    def shutdown(self) -> None:
        self._stop_engine()

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
        }

    # -- AudioOutput --------------------------------------------------------

    def set_ptt(self, active: bool) -> None:
        if self.engine is not None:
            self.engine.processor.set_ptt(active)

    def set_repeating(self, repeating: bool) -> None:
        if self.engine is not None:
            self.engine.processor.set_repeating(repeating)

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
            self.error = "COS source is CM108, but no CM108 interface is configured (MOREOPENREPEATER_CM108_HIDRAW)"
            return
        rate = self._renderer.sample_rate
        processor = AudioProcessor(
            ProcessorSettings(
                sample_rate=rate,
                cos_source="external" if cos_source == "cm108" else cos_source,
                **{field: getattr(config, field) for field in _LIVE_FIELDS},
            )
        )
        processor.set_ptt(self._service.ptt_active)
        processor.set_repeating(self._service.controller.state == RECEIVING)
        engine = self._engine_factory(
            processor,
            int(rate * BLOCK_SECONDS),
            on_events=self._on_events,
            input_device=config.audio_input_device or None,
            output_device=config.audio_output_device or None,
            ptt_output=self._cm108.set_ptt if self._cm108 else None,
            cos_input=self._cm108.read_cos if self._cm108 and cos_source == "cm108" else None,
        )
        try:
            engine.start()
        except Exception as error:  # PortAudio raises its own error types
            self.error = f"couldn't open audio devices: {error}"
            _logger.error("%s", self.error)
            try:
                engine.stop()
            except Exception:
                pass
            return
        self.engine = engine
        self.error = None

    def _stop_engine(self) -> None:
        if self.engine is not None:
            processor = self.engine.processor
            self.engine.stop()
            self.engine = None
            # Otherwise the controller would sit in RECEIVING until the timeout timer.
            released = []
            if processor.cos_open:
                released.append(COSChanged(active=False))
            if processor.ctcss_hz is not None:
                released.append(CTCSSChanged(tone_hz=None))
            if released:
                self._service.handle_audio_events(released)
        self.error = None
