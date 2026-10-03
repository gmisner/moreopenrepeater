"""The CM108 interface's spare GPIO pins: outputs switched from the dashboard
or a DTMF macro (a relay, a fan, a remote reset), inputs shown on the
dashboard (a door switch, a power-fail alarm).

Which pins are used, and how, is `RepeaterConfig.gpio_pins`. Outputs start
low, and a pin taken out of use goes back to being an input. Output levels
aren't saved: after a restart every output is off.

An input can say something or run a DTMF macro when it turns on or off. A
change counts once it has held for `SETTLE_SECONDS`, so a bouncing contact
acts once; the reading at startup doesn't count as a change.

Outputs can also be switched on for a weekly window (`gpio_schedules`, see
`api.weekly`) and off at its end. Switching one off by hand keeps it off for
the rest of that window; after a restart, an output inside its window comes
back on.

A pin given to the transmitter fan (`api.tx_fan`) is the fan's; it's only
reported here.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Callable, Optional

from audio_io.cm108 import CM108Interface
from controller.state_machine import RepeaterConfig
from playout.renderer import TTS_PREFIX

from .tx_fan import fan_cm108_pin
from .weekly import last_start, next_start, window_end

if TYPE_CHECKING:
    from .service import RepeaterService

_logger = logging.getLogger("moreopenrepeater.gpio")

SPARE_PINS = (1, 2, 4, 5, 6, 7, 8)  # GPIO3 is PTT
POLL_SECONDS = 0.25
SETTLE_SECONDS = 1.0
SCHEDULE_POLL_SECONDS = 5.0
STAY_GRACE = timedelta(minutes=5)  # schedules with no end only switch on this soon after the start
DEFAULT_PULSE_SECONDS = 1.0
MAX_PULSE_SECONDS = 60.0

_COMMAND = re.compile(r"^\s*([1245678])\s+(on|off|toggle|pulse)(?:\s+(\d+(?:\.\d+)?))?\s*$", re.IGNORECASE)


class GpioError(Exception):
    pass


def parse_gpio_command(text: str) -> tuple[int, str, float]:
    """A macro's argument, `<pin> on|off|toggle|pulse [seconds]`."""
    match = _COMMAND.match(text)
    if match is None:
        raise ValueError("expected a pin and on, off, toggle or pulse, like '1 on' or '2 pulse 3'")
    pin, verb, seconds = int(match[1]), match[2].lower(), match[3]
    if seconds is not None and verb != "pulse":
        raise ValueError("only pulse takes a time")
    duration = float(seconds) if seconds is not None else DEFAULT_PULSE_SECONDS
    if not 0 < duration <= MAX_PULSE_SECONDS:
        raise ValueError(f"a pulse lasts up to {MAX_PULSE_SECONDS:g} seconds")
    return pin, verb, duration


def _pins(config: RepeaterConfig) -> dict[int, dict]:
    return {int(pin): settings for pin, settings in config.gpio_pins.items()}


class GpioControl:
    def __init__(
        self,
        service: RepeaterService,
        cm108: Optional[CM108Interface],
        poll_seconds: float = POLL_SECONDS,
        settle_seconds: float = SETTLE_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._service = service
        self._cm108 = cm108
        self._poll_seconds = poll_seconds
        self._settle_seconds = settle_seconds
        self._clock = clock
        self._inputs: dict[int, bool] = {}
        self._settled: dict[int, bool] = {}  # raw level last acted on
        self._changed_at: dict[int, float] = {}
        self._pulses: dict[int, asyncio.TimerHandle] = {}
        self._task: Optional[asyncio.Task] = None
        self._schedule_task: Optional[asyncio.Task] = None
        self.error: Optional[str] = None
        self.scheduler = GpioScheduler(self, lambda: self._service.config.gpio_schedules)

    def start(self) -> None:
        self._service.add_config_listener(self._apply)
        self._apply(self._service.config)
        if self._cm108 is not None:
            self._task = asyncio.create_task(self._poll())
            self._schedule_task = asyncio.create_task(self._run_schedule())

    async def stop(self) -> None:
        for handle in self._pulses.values():
            handle.cancel()
        self._pulses.clear()
        for task in (self._task, self._schedule_task):
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._task = self._schedule_task = None

    def status(self) -> dict:
        config = self._service.config
        pins = _pins(config)
        fan_pin = fan_cm108_pin(config)
        rows = []
        for pin in SPARE_PINS:
            if pin == fan_pin:
                level = self._cm108.output_level(pin) if self._cm108 is not None else None
                on = None if level is None else level != (config.fan_polarity == "low")
                rows.append({"pin": pin, "name": "Transmitter fan", "mode": "fan", "on": on, "until": None})
                continue
            settings = pins.get(pin, {})
            mode = settings.get("mode")
            if mode == "output" and self._cm108 is not None:
                on = self._cm108.output_level(pin)
            elif mode == "input":
                on = self._inputs.get(pin)
            else:
                on = None
            until = self.scheduler.until(pin) if mode == "output" else None
            rows.append({"pin": pin, "name": settings.get("name", ""), "mode": mode, "on": on, "until": until})
        return {
            "available": self._cm108 is not None,
            "error": self.error,
            "pins": rows,
            "schedules": self.scheduler.status(self._service.wall_now()),
        }

    def set_output(self, pin: int, on: bool) -> None:
        self._output(pin)
        self._cancel_pulse(pin)
        self._write(pin, on)

    def toggle(self, pin: int) -> bool:
        on = not self._cm108_level(pin)
        self.set_output(pin, on)
        return on

    def pulse(self, pin: int, seconds: float) -> None:
        self.set_output(pin, True)
        self._pulses[pin] = asyncio.get_running_loop().call_later(seconds, self._end_pulse, pin)

    def run_command(self, text: str) -> str:
        """Run a DTMF macro's `<pin> on|off|toggle|pulse [seconds]`; returns what to say."""
        try:
            pin, verb, seconds = parse_gpio_command(text)
            name = self.name(pin)
            if verb == "toggle":
                verb = "on" if self.toggle(pin) else "off"
            elif verb == "pulse":
                self.pulse(pin, seconds)
                return f"{name} pulsed."
            else:
                self.set_output(pin, verb == "on")
            return f"{name} {verb}."
        except (ValueError, GpioError) as error:
            _logger.warning("GPIO macro %r: %s", text, error)
            return "That output is not set up."

    def name(self, pin: int) -> str:
        return _pins(self._service.config).get(pin, {}).get("name") or f"Output {pin}"

    # -- internals ----------------------------------------------------------

    def _output(self, pin: int) -> None:
        if self._cm108 is None:
            raise GpioError("No CM108 interface was found when the service started.")
        if _pins(self._service.config).get(pin, {}).get("mode") != "output":
            raise GpioError(f"GPIO{pin} isn't set up as an output.")

    def _cm108_level(self, pin: int) -> bool:
        self._output(pin)
        return bool(self._cm108.output_level(pin))

    def _write(self, pin: int, on: bool) -> None:
        try:
            self._cm108.set_gpio(pin, on)
        except OSError as error:
            self.error = f"couldn't set GPIO{pin}: {error}"
            raise GpioError(self.error) from error
        self.error = None
        _logger.info("GPIO%d (%s) %s", pin, self.name(pin), "on" if on else "off")

    def _cancel_pulse(self, pin: int) -> None:
        handle = self._pulses.pop(pin, None)
        if handle is not None:
            handle.cancel()

    def _end_pulse(self, pin: int) -> None:
        self._pulses.pop(pin, None)
        try:
            self._output(pin)
            self._write(pin, False)
        except GpioError as error:
            _logger.warning("ending the pulse on GPIO%d: %s", pin, error)

    def _apply(self, config: RepeaterConfig) -> None:
        if self._cm108 is None:
            return
        pins = _pins(config)
        fan_pin = fan_cm108_pin(config)
        for pin in SPARE_PINS:
            mode = pins.get(pin, {}).get("mode")
            try:
                if pin == fan_pin:
                    self._cancel_pulse(pin)
                elif mode == "output":
                    if self._cm108.output_level(pin) is None:
                        self._cm108.set_gpio(pin, False)
                else:
                    self._cancel_pulse(pin)
                    self._cm108.release_gpio(pin)
            except OSError as error:
                self.error = f"couldn't set up GPIO{pin}: {error}"
                _logger.error("%s", self.error)
            if mode != "input":
                self._inputs.pop(pin, None)
                self._settled.pop(pin, None)
                self._changed_at.pop(pin, None)

    async def _poll(self) -> None:
        assert self._cm108 is not None
        while True:
            await asyncio.sleep(self._poll_seconds)
            inputs = {pin: s for pin, s in _pins(self._service.config).items() if s.get("mode") == "input"}
            if not inputs:
                continue
            try:
                _buttons, levels = await asyncio.to_thread(self._cm108.read_inputs)
            except OSError as error:
                if self.error is None:
                    _logger.error("reading the GPIO inputs failed: %s", error)
                self.error = f"couldn't read the GPIO inputs: {error}"
                await asyncio.sleep(1.0)
                continue
            self.error = None
            for pin, settings in inputs.items():
                level = bool(levels & (1 << (pin - 1)))
                on = level != bool(settings.get("invert"))
                if self._inputs.get(pin) != on:
                    _logger.info("GPIO%d (%s) input %s", pin, self.name(pin), "on" if on else "off")
                self._inputs[pin] = on
                self._settle(pin, level)

    async def _run_schedule(self) -> None:
        while True:
            try:
                self.scheduler.tick(self._service.wall_now())
            except Exception:
                _logger.exception("unexpected error in the GPIO schedule")
            await asyncio.sleep(SCHEDULE_POLL_SECONDS)

    def _settle(self, pin: int, level: bool) -> None:
        settled = self._settled.get(pin)
        if settled is None:
            self._settled[pin] = level
            return
        if level == settled:
            self._changed_at.pop(pin, None)
            return
        since = self._changed_at.setdefault(pin, self._clock())
        if self._clock() - since >= self._settle_seconds:
            self._settled[pin] = level
            del self._changed_at[pin]
            self._act(pin)

    def _act(self, pin: int) -> None:
        settings = _pins(self._service.config).get(pin, {})
        on = self._settled[pin] != bool(settings.get("invert"))
        state = "on" if on else "off"
        say = settings.get(f"{state}_say", "").strip()
        macro = settings.get(f"{state}_macro", "")
        if say:
            _logger.info("GPIO%d (%s) %s: saying %r", pin, self.name(pin), state, say)
            self._service.speak(TTS_PREFIX + say)
        if macro:
            self._service.run_macro(macro, f"GPIO{pin}")


class GpioScheduler:
    def __init__(self, gpio: GpioControl, schedules: Callable[[], list[dict]]) -> None:
        self._gpio = gpio
        self._schedules = schedules
        self._handled: set[tuple[int, datetime]] = set()
        self._failed: set[tuple[int, datetime]] = set()
        self._ours: dict[int, datetime] = {}  # pin -> when the schedule switches it off

    def until(self, pin: int) -> Optional[datetime]:
        return self._ours.get(pin)

    def status(self, now: datetime) -> list[dict]:
        """For each schedule, in order: its next start and, while it has the output on, when it ends."""
        rows = []
        for schedule in self._schedules():
            until = self._ours.get(schedule["pin"])
            start = last_start(schedule, now)
            active = until is not None and start is not None and window_end(schedule, start) == until
            rows.append({"pin": schedule["pin"], "next_start": next_start(schedule, now), "until": until if active else None})
        return rows

    def tick(self, now: datetime) -> None:
        for pin, end in list(self._ours.items()):
            if now >= end:
                del self._ours[pin]
                try:
                    self._gpio.set_output(pin, False)
                except GpioError as error:
                    _logger.warning("couldn't switch off scheduled GPIO%d: %s", pin, error)
        for schedule in self._schedules():
            if schedule.get("enabled", True):
                self._start(schedule, now)

    def _start(self, schedule: dict, now: datetime) -> None:
        start = last_start(schedule, now)
        if start is None:
            return
        end = window_end(schedule, start)
        if now >= (end or start + STAY_GRACE):
            return
        pin = schedule["pin"]
        key = (pin, start)
        if key in self._handled:
            return
        try:
            self._gpio.set_output(pin, True)
        except GpioError as error:
            if key not in self._failed:
                self._failed.add(key)
                _logger.warning("couldn't switch on scheduled GPIO%d: %s", pin, error)
            return
        self._handled.add(key)
        cutoff = now - timedelta(days=8)
        self._handled = {k for k in self._handled if k[1] > cutoff}
        self._failed = {k for k in self._failed if k[1] > cutoff}
        if end is not None:
            self._ours[pin] = max(end, self._ours.get(pin, end))
