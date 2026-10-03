"""Runs the link radio as a remote base: DTMF tuning, and CAT control
through rigctld (api.rig_control).

The frequency, shift and tone live in the settings, so the dashboard and
DTMF tune the same way: a change is saved, then sent to the radio. DTMF
tuning answers on the air with what the radio was set to, or that it didn't
respond. Frequencies outside `remote_base_ranges` (checked on both the
receive and the transmit side of a shift) are refused.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Callable, Optional

from controller.remote_base import in_ranges, offset_mhz, parse_frequency, parse_ranges, parse_tone, spoken_mhz, summary, transmit_mhz
from controller.state_machine import RepeaterConfig
from playout.renderer import TTS_PREFIX

from .rig_control import RigError, Rigctld, Tuning
from .service import RepeaterService

_logger = logging.getLogger("moreopenrepeater.remote_base")


def wanted_tuning(config: RepeaterConfig) -> Optional[Tuning]:
    """What the radio should be set to, or None when there's no CAT control."""
    if config.link_radio_mode != "remote_base" or not config.remote_base_rigctld:
        return None
    mhz, shift = config.remote_base_mhz, config.remote_base_shift
    return Tuning(mhz, shift, offset_mhz(mhz, shift, config.remote_base_offset_mhz), config.remote_base_tone_hz, config.remote_base_rig_mode)


def spoken_state(config: RepeaterConfig) -> str:
    return summary(config.remote_base_mhz, config.remote_base_shift, config.remote_base_tone_hz)


class RemoteBase:
    def __init__(self, service: RepeaterService, rig_factory: Callable[[str], Rigctld] = Rigctld) -> None:
        self._service = service
        self._rig_factory = rig_factory
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._sent: Optional[Tuning] = None  # what the radio was last set to
        self._announce = False  # say the result of the next send on the air
        self.error: Optional[str] = None
        service.remote_base_hook = self._dtmf

    def attach(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._service.add_config_listener(self._config_changed)
        self._config_changed(self._service.config)

    def status(self) -> dict:
        return {"rig_error": self.error, "tuned": self._sent is not None and self._sent == wanted_tuning(self._service.config)}

    def retune(self) -> None:
        """Send the settings to the radio again (it may have been retuned by hand)."""
        self._sent = None
        self._config_changed(self._service.config)

    # -- internals ------------------------------------------------------------

    def _config_changed(self, config: RepeaterConfig) -> None:
        wanted = wanted_tuning(config)
        if wanted is None:
            self._sent = None
            self.error = None
            return
        if wanted != self._sent:
            self._send(wanted, config.remote_base_rigctld)

    def _send(self, tuning: Tuning, address: str) -> None:
        announce, self._announce = self._announce, False

        def done(error: Optional[str]) -> None:
            self.error = error
            if error is None:
                self._sent = tuning
            else:
                _logger.warning("remote base: %s", error)
            if announce:
                self._say(f"Remote base {spoken_state(self._service.config)}." if error is None else "The remote base radio did not respond.")

        def send() -> Optional[str]:
            try:
                self._rig_factory(address).tune(tuning)
                return None
            except RigError as error:
                return str(error)

        if self._loop is None:
            done(send())
            return
        future = self._loop.run_in_executor(None, send)
        future.add_done_callback(lambda f: done(f.result() if f.exception() is None else str(f.exception())))

    def _say(self, text: str) -> None:
        self._service.speak(TTS_PREFIX + text)

    def _dtmf(self, action: str, argument: str) -> None:
        config = self._service.config
        if config.link_radio_mode != "remote_base":
            self._say("The remote base is not set up.")
            return
        if action == "remote_status":
            state = "on" if config.link_radio_enabled else "off"
            self._say(f"Remote base {state}, {spoken_state(config)}.")
            return
        if not config.remote_base_rigctld:
            self._say("The remote base has no radio control.")
            return
        changes: dict = {}
        if action == "remote_tune":
            mhz = parse_frequency(argument)
            if mhz is None:
                self._say("That frequency is not valid.")
                return
            changes = {"remote_base_mhz": mhz, "remote_base_shift": "simplex"}
        elif action == "remote_tone":
            tone = parse_tone(argument)
            if tone is None:
                self._say("That tone is not valid.")
                return
            changes = {"remote_base_tone_hz": tone or None}
        elif action == "remote_shift":
            changes = {"remote_base_shift": argument}
        mhz = changes.get("remote_base_mhz", config.remote_base_mhz)
        shift = changes.get("remote_base_shift", config.remote_base_shift)
        ranges = parse_ranges(config.remote_base_ranges)
        if not in_ranges(mhz, ranges) or not in_ranges(transmit_mhz(mhz, shift, config.remote_base_offset_mhz), ranges):
            self._say(f"{spoken_mhz(mhz)} is outside the remote base's bands.")
            return
        self._announce = True
        self._service.update_config(**changes)
        if self._announce:  # nothing to send: the radio is already there
            self._announce = False
            self._say(f"Remote base {spoken_state(self._service.config)}.")
