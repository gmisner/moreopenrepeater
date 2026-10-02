"""Runs a link radio: a second radio, on its own sound device, linking the
repeater to a far station (another repeater's input, or a simplex link).

    repeater user --> repeater RX --+--> repeater TX
                                    +--> link radio TX --> far station
    far station --> link radio RX ----> repeater TX (as a remote key-up)

  - The link radio has its own `AudioEngine`: its own COS (VOX, CTCSS, a
    Pi header pin or a second CM108) and its own PTT (a Pi header pin or a
    second CM108).
  - Audio crosses between the two engines through a pair of `LinkAudio`
    buffers, moved every 20 ms on the event loop.
  - The far end keys the repeater like a linked node
    (`RepeaterService.link_radio_keyed`): link courtesy tone, shared timeout.
  - `controller.link_radio` decides when the link transmitter keys, and
    when it sends its timeout tone, courtesy tone and ID.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Callable, Optional

from audio_io.cm108 import CM108Interface
from audio_io.engine import AudioEngine
from audio_io.patch import LinkAudio
from audio_io.pi_gpio import GpioLine, open_header_pin
from audio_io.processor import AudioProcessor, ProcessorSettings
from controller.events import COSChanged
from controller.link_radio import LinkPlay, LinkPTT, LinkRadioController, LinkRadioSettings
from controller.state_machine import RepeaterConfig
from playout.renderer import ClipRenderer, UnknownClipError
from playout.tts import TTSError

from .service import RepeaterService

_logger = logging.getLogger("moreopenrepeater.link_radio")

BLOCK_SECONDS = 0.02
UPDATE_SECONDS = 0.05
_RESTART_FIELDS = (
    "link_radio_enabled", "link_radio_input_device", "link_radio_output_device", "link_radio_cos",
    "link_radio_cos_polarity", "link_radio_cos_gpio_pin", "link_radio_ptt", "link_radio_ptt_gpio_pin",
    "link_radio_ptt_polarity",
    "audio_enabled", "audio_input_device", "audio_output_device", "cos_source", "cos_gpio_pin", "ptt_output",
    "ptt_gpio_pin", "monitor_enabled", "monitor_input_device", "monitor_squelch", "monitor_gpio_pin",
)


def setup_problem(config: RepeaterConfig, cm108: Optional[CM108Interface]) -> Optional[str]:
    """Why these settings can't run the link radio, or None."""
    if not config.audio_enabled:
        return "The link radio needs the repeater's live audio turned on."
    if config.link_radio_input_device == config.audio_input_device:
        return "The link radio needs its own input device, not the repeater's receiver."
    if config.link_radio_output_device == config.audio_output_device:
        return "The link radio needs its own output device, not the repeater's transmitter."
    if config.monitor_enabled and config.link_radio_input_device == config.monitor_input_device:
        return "The link radio's input device is the monitor receiver's."
    taken = {}
    if config.cos_source == "gpio":
        taken[config.cos_gpio_pin] = "the repeater's COS pin"
    if config.ptt_output == "gpio":
        taken[config.ptt_gpio_pin] = "the repeater's PTT pin"
    if config.monitor_enabled and config.monitor_squelch == "gpio":
        taken[config.monitor_gpio_pin] = "the monitor receiver's squelch pin"
    pins = []
    if config.link_radio_cos == "gpio":
        pins.append(config.link_radio_cos_gpio_pin)
    if config.link_radio_ptt == "gpio":
        pins.append(config.link_radio_ptt_gpio_pin)
    if len(pins) == 2 and pins[0] == pins[1]:
        return f"The link radio's PTT and COS can't both use GPIO{pins[0]}."
    for pin in pins:
        if pin in taken:
            return f"GPIO{pin} is already {taken[pin]}."
    if cm108 is None and "cm108" in (config.link_radio_cos, config.link_radio_ptt):
        return "The link radio is set to use a CM108, but no second CM108 interface was found."
    return None


class LinkRadio:
    def __init__(
        self,
        service: RepeaterService,
        renderer: ClipRenderer,
        sending: Callable[[], bool],
        attach_port: Callable[[Optional[LinkAudio]], None],
        find_cm108: Callable[[], Optional[CM108Interface]] = lambda: None,
        engine_factory: Callable[..., AudioEngine] = AudioEngine,
        open_pin: Callable[..., GpioLine] = open_header_pin,
        clock: Callable[[], float] = lambda: asyncio.get_running_loop().time(),
    ) -> None:
        """`sending()`: the repeater is repeating a local user right now.
        `attach_port` hands the repeater's half of the audio crossing to
        its engine (`LiveAudio.set_port`)."""
        self._service = service
        self._renderer = renderer
        self._sending = sending
        self._attach_port = attach_port
        self._find_cm108 = find_cm108
        self._cm108: Optional[CM108Interface] = None
        self._cm108_looked = False
        self._engine_factory = engine_factory
        self._open_pin = open_pin
        self._clock = clock
        self._pins: list[GpioLine] = []
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._applied: Optional[RepeaterConfig] = None
        self.engine: Optional[AudioEngine] = None
        self.error: Optional[str] = None
        self.controller = LinkRadioController()
        self.receiving = False
        # Repeater side (in the repeater's engine) and link side (in this engine).
        self._repeater_side: Optional[LinkAudio] = None
        self._link_side: Optional[LinkAudio] = None

    def attach(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._service.add_config_listener(self.apply_config)
        self.apply_config(self._service.config)

    def shutdown(self) -> None:
        self._stop()

    def apply_config(self, config: RepeaterConfig) -> None:
        previous, self._applied = self._applied, config
        self.controller.settings = LinkRadioSettings(
            timeout=config.link_radio_timeout,
            courtesy_tone=config.link_radio_courtesy_tone,
            id_interval=config.id_interval,
        )
        if previous is None or any(getattr(previous, f) != getattr(config, f) for f in _RESTART_FIELDS):
            self._stop()
            if config.link_radio_enabled:
                self._start(config)
        elif self.engine is not None:
            settings = self.engine.processor.settings
            settings.vox_threshold_db = config.link_radio_vox_threshold_db
            settings.tx_gain_db = config.link_radio_tx_gain_db
            settings.tx_ctcss_hz = config.link_radio_tx_ctcss_hz
            settings.squelch_tail_ms = config.link_radio_squelch_tail_ms
            settings.tx_delay_ms = config.link_radio_tx_delay_ms
            settings.dtmf_mute = config.dtmf_mute

    def status(self) -> dict:
        config = self._service.config
        engine = self.engine
        return {
            "enabled": config.link_radio_enabled,
            "running": engine is not None and engine.running,
            "error": self.error,
            "name": config.link_radio_name,
            "rx_level_db": engine.processor.rx_level_db if engine else -120.0,
            "receiving": self.receiving,
            "transmitting": engine.transmitting if engine else False,
            "timed_out": self.controller.timed_out,
            "id_owed": self.controller.id_owed,
            "device_sample_rate": engine.device_sample_rate if engine else None,
            "dropped_input_blocks": engine.dropped_input_blocks if engine else 0,
            "starved_output_blocks": engine.starved_output_blocks if engine else 0,
        }

    async def run(self) -> None:
        """Moves audio across every 20 ms and updates the transmitter."""
        last_update = 0.0
        while True:
            await asyncio.sleep(BLOCK_SECONDS)
            self.pump()
            now = self._clock()
            if now - last_update >= UPDATE_SECONDS:
                last_update = now
                self.update(now)

    def pump(self) -> None:
        repeater, link = self._repeater_side, self._link_side
        if repeater is None or link is None:
            return
        while (block := repeater.take_radio()) is not None:
            link.add_phone(block)
        while (block := link.take_radio()) is not None:
            repeater.add_phone(block)

    def update(self, now: float) -> None:
        engine = self.engine
        if engine is None:
            return
        for command in self.controller.update(now, self._sending(), self.receiving):
            if isinstance(command, LinkPTT):
                engine.processor.set_ptt(command.active)
            elif isinstance(command, LinkPlay):
                self._play(engine, command.clip)

    # -- internals ------------------------------------------------------------

    def _play(self, engine: AudioEngine, clip: str) -> None:
        config = self._service.config
        samples = self._renderer.cached_samples(clip, config)
        if samples is not None:
            engine.processor.play(samples)
        elif self._loop is not None:
            self._loop.run_in_executor(None, self._render_and_play, engine, clip, config)

    def _render_and_play(self, engine: AudioEngine, clip: str, config: RepeaterConfig) -> None:
        try:
            engine.processor.play(self._renderer.render(clip, config))
        except (UnknownClipError, TTSError):
            _logger.exception("couldn't render %s for the link radio", clip)

    def _on_events(self, events) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._handle_events, events)

    def _handle_events(self, events) -> None:
        for event in events:
            if isinstance(event, COSChanged) and event.active != self.receiving and self.engine is not None:
                self._set_receiving(event.active)

    def _set_receiving(self, receiving: bool) -> None:
        self.receiving = receiving
        self._service.link_radio_keyed(receiving)

    def _cm108_interface(self) -> Optional[CM108Interface]:
        if not self._cm108_looked:
            self._cm108_looked = True
            self._cm108 = self._find_cm108()
        return self._cm108

    def _start(self, config: RepeaterConfig) -> None:
        wants_cm108 = "cm108" in (config.link_radio_cos, config.link_radio_ptt)
        cm108 = self._cm108_interface() if wants_cm108 else None
        problem = setup_problem(config, cm108)
        if problem:
            self.error = problem
            return
        ptt_output = cm108.set_ptt if cm108 is not None and config.link_radio_ptt == "cm108" else None
        cos_input = cm108.read_cos if cm108 is not None and config.link_radio_cos == "cm108" else None
        if cm108 is not None:
            cm108.cos_active_low = config.link_radio_cos_polarity == "low"
        try:
            if config.link_radio_ptt == "gpio":
                ptt_output = self._claim_pin(
                    config.link_radio_ptt_gpio_pin, "PTT", output=True, active_low=config.link_radio_ptt_polarity == "low"
                ).write
            if config.link_radio_cos == "gpio":
                cos_input = self._claim_pin(
                    config.link_radio_cos_gpio_pin, "COS", output=False, active_low=config.link_radio_cos_polarity == "low"
                ).read
        except OSError as error:
            self._release_pins()
            self.error = str(error)
            _logger.error("%s", self.error)
            return
        rate = self._renderer.sample_rate
        processor = AudioProcessor(
            ProcessorSettings(
                sample_rate=rate,
                cos_source="external" if config.link_radio_cos in ("cm108", "gpio") else config.link_radio_cos,
                vox_threshold_db=config.link_radio_vox_threshold_db,
                tx_gain_db=config.link_radio_tx_gain_db,
                tx_ctcss_hz=config.link_radio_tx_ctcss_hz,
                squelch_tail_ms=config.link_radio_squelch_tail_ms,
                tx_delay_ms=config.link_radio_tx_delay_ms,
                dtmf_mute=config.dtmf_mute,
                local_repeat=False,
            )
        )
        self._link_side, self._repeater_side = LinkAudio(rate), LinkAudio(rate)
        # Always "repeating", so the far end goes to the repeater whenever its COS is open.
        processor.set_repeating(True)
        processor.set_link(self._link_side)
        engine = self._engine_factory(
            processor,
            int(rate * BLOCK_SECONDS),
            on_events=self._on_events,
            input_device=config.link_radio_input_device or None,
            output_device=config.link_radio_output_device or None,
            ptt_output=ptt_output,
            cos_input=cos_input,
        )
        try:
            engine.start()
        except Exception as error:  # PortAudio raises its own error types
            self.error = f"couldn't open the link radio's audio devices: {error}"
            _logger.error("%s", self.error)
            try:
                engine.stop()
            except Exception:
                pass
            self._release_pins()
            self._link_side = self._repeater_side = None
            return
        self.engine = engine
        self.error = None
        self._attach_port(self._repeater_side)

    def _claim_pin(self, pin: int, role: str, *, output: bool, active_low: bool) -> GpioLine:
        try:
            line = self._open_pin(pin, output=output, active_low=active_low)
        except OSError as error:
            raise OSError(f"couldn't open GPIO{pin} for the link radio's {role}: {error.strerror or error}") from error
        self._pins.append(line)
        return line

    def _release_pins(self) -> None:
        for line in self._pins:
            try:
                line.close()
            except OSError:
                pass
        self._pins = []

    def _stop(self) -> None:
        if self.engine is not None:
            self._attach_port(None)
            self.engine.stop()
            self.engine = None
            self._release_pins()
        self._link_side = self._repeater_side = None
        self.controller.reset()
        if self.receiving:
            # Otherwise the repeater would stay keyed for the far end.
            self._set_receiving(False)
        self.error = None
