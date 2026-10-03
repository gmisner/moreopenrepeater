"""A transmitter cooling fan on an output that follows PTT: on when the
transmitter keys, and off once it has been idle for `fan_run_on_minutes`.
With `fan_temp_c` set it also runs while the CPU is at least that hot, until
it has cooled `TEMPERATURE_HYSTERESIS_C` below.

The output is a spare CM108 pin (one not set up on the GPIO card) or a
Raspberry Pi header pin. It starts off, and goes off when the service stops.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Callable, Optional

from audio_io.cm108 import CM108Interface
from audio_io.pi_gpio import GpioLine, open_header_pin
from controller.state_machine import RepeaterConfig

if TYPE_CHECKING:
    from .service import RepeaterService

_logger = logging.getLogger("moreopenrepeater.tx_fan")

POLL_SECONDS = 0.25
TEMPERATURE_POLL_SECONDS = 10.0
TEMPERATURE_HYSTERESIS_C = 5.0


def fan_cm108_pin(config: RepeaterConfig) -> Optional[int]:
    """The CM108 pin the fan has, or None; a pin set up on the GPIO card stays the card's."""
    if config.fan_output != "cm108" or str(config.fan_cm108_pin) in config.gpio_pins:
        return None
    return config.fan_cm108_pin


def _pi_pins_in_use(config: RepeaterConfig) -> dict[int, str]:
    taken = {}
    if config.audio_enabled:
        if config.cos_source == "gpio":
            taken[config.cos_gpio_pin] = "the repeater's COS pin"
        if config.ptt_output == "gpio":
            taken[config.ptt_gpio_pin] = "the repeater's PTT pin"
    if config.monitor_enabled and config.monitor_squelch == "gpio":
        taken[config.monitor_gpio_pin] = "the monitor receiver's squelch pin"
    if config.link_radio_enabled:
        if config.link_radio_cos == "gpio":
            taken[config.link_radio_cos_gpio_pin] = "the link radio's COS pin"
        if config.link_radio_ptt == "gpio":
            taken[config.link_radio_ptt_gpio_pin] = "the link radio's PTT pin"
    return taken


def setup_problem(config: RepeaterConfig, cm108: Optional[CM108Interface]) -> Optional[str]:
    """Why the fan's output can't be used, or None."""
    if config.fan_output == "cm108":
        if cm108 is None:
            return "No CM108 interface was found when the service started."
        if fan_cm108_pin(config) is None:
            return f"GPIO{config.fan_cm108_pin} is set up on the CM108 GPIO card too."
    elif config.fan_output == "gpio":
        owner = _pi_pins_in_use(config).get(config.fan_gpio_pin)
        if owner:
            return f"GPIO{config.fan_gpio_pin} is already {owner}."
    return None


class TxFan:
    def __init__(
        self,
        service: RepeaterService,
        cm108: Optional[CM108Interface],
        open_pin: Callable[..., GpioLine] = open_header_pin,
        temperature: Callable[[], Optional[float]] = lambda: None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._service = service
        self._cm108 = cm108
        self._open_pin = open_pin
        self._temperature = temperature
        self._clock = clock
        self._claimed: Optional[tuple] = None  # (output, pin, polarity) held now
        self._write: Optional[Callable[[bool], None]] = None
        self._release: Callable[[], None] = lambda: None
        self._on: Optional[bool] = None  # None: not known to be either, write it next tick
        self._keyed_at: Optional[float] = None
        self._hot = False
        self._temperature_at: Optional[float] = None
        self.temperature_c: Optional[float] = None
        self.reason: Optional[str] = None  # "transmitting", "run-on" or "hot"
        self._problem: Optional[str] = None
        self._write_error: Optional[str] = None
        self._task: Optional[asyncio.Task] = None

    @property
    def error(self) -> Optional[str]:
        return self._problem or self._write_error

    def start(self) -> None:
        self._service.add_config_listener(self._apply)
        self._apply(self._service.config)
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self._drop()

    def status(self) -> dict:
        config = self._service.config
        pin = {"cm108": config.fan_cm108_pin, "gpio": config.fan_gpio_pin}.get(config.fan_output)
        run_on_left = None
        if self.reason == "run-on" and self._keyed_at is not None:
            run_on_left = max(0.0, config.fan_run_on_minutes * 60 - (self._clock() - self._keyed_at))
        return {
            "output": config.fan_output,
            "pin": pin,
            "on": self._on if self._write is not None else None,
            "reason": self.reason if self._on else None,
            "run_on_left": run_on_left,
            "temperature_c": self.temperature_c,
            "error": self.error,
        }

    def tick(self) -> None:
        config = self._service.config
        now = self._clock()
        transmitting = self._service.ptt_active
        if transmitting:
            self._keyed_at = now
        self._check_temperature(config, now)
        if transmitting:
            self.reason = "transmitting"
        elif self._keyed_at is not None and now - self._keyed_at < config.fan_run_on_minutes * 60:
            self.reason = "run-on"
        elif self._hot:
            self.reason = "hot"
        else:
            self.reason = None
        want = self.reason is not None
        if self._write is None or want == self._on:
            return
        try:
            self._write(want)
        except OSError as error:
            if self._write_error is None:
                _logger.error("switching the fan %s failed: %s", "on" if want else "off", error)
            self._write_error = f"couldn't switch the fan: {error.strerror or error}"
            self._on = None
            return
        self._write_error = None
        self._on = want
        _logger.info("fan %s (%s)", "on" if want else "off", self.reason or "idle")

    # -- internals ----------------------------------------------------------

    async def _run(self) -> None:
        while True:
            try:
                self.tick()
            except Exception:
                _logger.exception("unexpected error running the fan")
            await asyncio.sleep(POLL_SECONDS)

    def _check_temperature(self, config: RepeaterConfig, now: float) -> None:
        limit = config.fan_temp_c
        if limit is None:
            self._hot = False
            return
        if self._temperature_at is not None and now - self._temperature_at < TEMPERATURE_POLL_SECONDS:
            return
        self._temperature_at = now
        self.temperature_c = self._temperature()
        if self.temperature_c is None:
            self._hot = False
        elif not self._hot and self.temperature_c >= limit:
            self._hot = True
        elif self._hot and self.temperature_c < limit - TEMPERATURE_HYSTERESIS_C:
            self._hot = False

    def _apply(self, config: RepeaterConfig) -> None:
        if config.fan_temp_c is None:
            self._temperature_at = None
        problem = setup_problem(config, self._cm108)
        wanted = None
        if config.fan_output != "none" and problem is None:
            pin = config.fan_cm108_pin if config.fan_output == "cm108" else config.fan_gpio_pin
            wanted = (config.fan_output, pin, config.fan_polarity)
        if wanted == self._claimed and problem == self._problem:
            return
        if problem and problem != self._problem:
            _logger.warning("fan: %s", problem)
        self._problem = problem
        if wanted == self._claimed:
            return
        self._drop()
        if wanted is not None:
            self._claim(*wanted)

    def _claim(self, output: str, pin: int, polarity: str) -> None:
        active_low = polarity == "low"
        try:
            if output == "cm108":
                cm108 = self._cm108
                assert cm108 is not None
                write = lambda on: cm108.set_gpio(pin, on != active_low)  # noqa: E731
                write(False)
                self._release = lambda: cm108.release_gpio(pin)
            else:
                line = self._open_pin(pin, output=True, active_low=active_low)
                write = line.write

                def release() -> None:
                    try:
                        line.write(False)
                    finally:
                        line.close()

                self._release = release
        except OSError as error:
            self._problem = f"couldn't open GPIO{pin} for the fan: {error.strerror or error}"
            _logger.error("%s", self._problem)
            return
        self._write = write
        self._claimed = (output, pin, polarity)
        self._on = False
        self._write_error = None
        _logger.info("fan on %s GPIO%d", "the CM108's" if output == "cm108" else "Pi header", pin)

    def _drop(self) -> None:
        if self._write is not None:
            try:
                self._release()
            except OSError as error:
                _logger.warning("releasing the fan's pin: %s", error)
        self._write = None
        self._release = lambda: None
        self._claimed = None
        self._on = None
        self.reason = None
