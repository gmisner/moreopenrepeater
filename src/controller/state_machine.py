"""Event-driven repeater state machine.

Callers feed real-world events via handle_event() and advance internal
timers via tick(now) -- there are no threads and no real time.sleep, so the
whole thing can be driven deterministically from tests with a fake clock.

Implemented as an explicit hand-rolled state machine rather than a generic
FSM library: with six states and a handful of transitions, an explicit
dispatch is easier to read, easier to test exhaustively, and avoids an
extra dependency for logic this size.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal, Optional

from .events import (
    AssertPTT,
    COSChanged,
    CTCSSChanged,
    ControllerCommand,
    ControllerEvent,
    DTMFDigit,
    LinkStateChanged,
    PlayAudio,
    RemoteKeyed,
)
from .macros import DTMFCommandDecoder, Macro

IDLE = "idle"
RECEIVING = "receiving"
COURTESY_TONE = "courtesy_tone"
HANG_TIME = "hang_time"
TIMEOUT = "timeout"
TRANSMITTING_ID = "transmitting_id"

_logger = logging.getLogger("moreopenrepeater.controller")


@dataclass(frozen=True)
class RepeaterConfig:
    courtesy_tone_duration: float = 0.2
    hang_time: float = 3.0
    tot_duration: float = 180.0
    id_interval: float = 600.0
    id_audio_duration: float = 3.0
    require_ctcss_hz: Optional[float] = None
    callsign: str = ""
    id_mode: Literal["voice", "cw", "both"] = "voice"
    cw_wpm: float = 20.0
    cw_tone_hz: float = 700.0
    courtesy_tone_asset_id: Optional[str] = None
    id_asset_id: Optional[str] = None
    timeout_tone_asset_id: Optional[str] = None
    aprs_enabled: bool = False
    aprs_server: str = "rotate.aprs2.net"
    aprs_port: int = 14580
    aprs_callsign: str = ""
    aprs_lat: Optional[float] = None
    aprs_lon: Optional[float] = None
    aprs_comment: str = ""
    aprs_beacon_interval: float = 1800.0


class RepeaterController:
    def __init__(
        self,
        config: RepeaterConfig,
        macros: Optional[list[Macro]] = None,
        now: float = 0.0,
    ) -> None:
        self.config = config
        self.state = IDLE
        self._dtmf = DTMFCommandDecoder(macros or [])
        self._ctcss_present = config.require_ctcss_hz is None
        self._tot_deadline: Optional[float] = None
        self._state_deadline: Optional[float] = None
        self._id_due_at = now + config.id_interval
        self._resume_state_after_id = IDLE
        self._remote_keyed: set[str] = set()

    # -- public API -------------------------------------------------------

    def list_macros(self) -> list[Macro]:
        return self._dtmf.list_macros()

    def set_macros(self, macros: list[Macro]) -> None:
        self._dtmf.set_macros(macros)

    def handle_event(self, event: ControllerEvent, now: float) -> list[ControllerCommand]:
        commands: list[ControllerCommand] = []

        if isinstance(event, COSChanged):
            commands += self._on_cos_changed(event.active, now)
        elif isinstance(event, CTCSSChanged):
            self._ctcss_present = (
                self.config.require_ctcss_hz is None or event.tone_hz == self.config.require_ctcss_hz
            )
        elif isinstance(event, DTMFDigit):
            command = self._dtmf.handle_digit(event.digit, now)
            if command is not None:
                commands.append(command)
        elif isinstance(event, RemoteKeyed):
            if event.keyed:
                self._remote_keyed.add(event.node_id)
                commands += self._on_cos_changed(True, now, remote=True)
            else:
                self._remote_keyed.discard(event.node_id)
                if not self._remote_keyed:
                    commands += self._on_cos_changed(False, now, remote=True)
        elif isinstance(event, LinkStateChanged):
            pass  # tracked by the link layer; no local repeater-state effect yet

        return commands

    def tick(self, now: float) -> list[ControllerCommand]:
        commands: list[ControllerCommand] = []

        if self.state == RECEIVING and self._tot_deadline is not None and now >= self._tot_deadline:
            commands += self._enter_timeout(now)

        if self._state_deadline is not None and now >= self._state_deadline:
            if self.state == COURTESY_TONE:
                commands += self._enter_hang_time(now)
            elif self.state == HANG_TIME:
                commands += self._enter_idle(now)
            elif self.state == TRANSMITTING_ID:
                commands += self._exit_id(now)

        self._dtmf.tick(now)

        if self.state in (IDLE, HANG_TIME) and now >= self._id_due_at:
            commands += self._enter_id(now)

        return commands

    # -- internal transitions ---------------------------------------------

    def _on_cos_changed(self, active: bool, now: float, remote: bool = False) -> list[ControllerCommand]:
        if active:
            if not remote and not self._ctcss_present:
                return []  # squelch open but wrong/no CTCSS -- ignore
            if self.state in (IDLE, HANG_TIME):
                return self._enter_receiving(now)
            return []

        if self.state == RECEIVING:
            if self._tot_deadline is not None and now >= self._tot_deadline:
                return self._enter_timeout(now)
            return self._enter_courtesy_tone(now)
        if self.state == TIMEOUT:
            return self._enter_idle(now)
        return []

    def _set_state(self, new_state: str) -> None:
        if new_state != self.state:
            _logger.info("state %s -> %s", self.state, new_state)
        self.state = new_state

    def _enter_receiving(self, now: float) -> list[ControllerCommand]:
        self._set_state(RECEIVING)
        self._tot_deadline = now + self.config.tot_duration
        self._state_deadline = None
        return [AssertPTT(active=True)]

    def _enter_courtesy_tone(self, now: float) -> list[ControllerCommand]:
        self._set_state(COURTESY_TONE)
        self._tot_deadline = None
        self._state_deadline = now + self.config.courtesy_tone_duration
        return [PlayAudio(clip="courtesy_tone")]

    def _enter_hang_time(self, now: float) -> list[ControllerCommand]:
        self._set_state(HANG_TIME)
        self._state_deadline = now + self.config.hang_time
        return []

    def _enter_idle(self, now: float) -> list[ControllerCommand]:
        self._set_state(IDLE)
        self._state_deadline = None
        self._tot_deadline = None
        return [AssertPTT(active=False)]

    def _enter_timeout(self, now: float) -> list[ControllerCommand]:
        self._set_state(TIMEOUT)
        self._tot_deadline = None
        self._state_deadline = None
        return [PlayAudio(clip="timeout_tone"), AssertPTT(active=False)]

    def _enter_id(self, now: float) -> list[ControllerCommand]:
        self._resume_state_after_id = self.state
        self._set_state(TRANSMITTING_ID)
        self._state_deadline = now + self.config.id_audio_duration
        self._id_due_at = now + self.config.id_interval
        return [AssertPTT(active=True), PlayAudio(clip="id")]

    def _exit_id(self, now: float) -> list[ControllerCommand]:
        target = self._resume_state_after_id
        self._set_state(target)
        if target == IDLE:
            self._state_deadline = None
            return [AssertPTT(active=False)]
        self._state_deadline = now + self.config.hang_time  # resume hang_time countdown
        return []
