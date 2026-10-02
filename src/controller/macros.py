"""DTMF command-macro dispatch, independent of the repeater state machine.

Digits accumulate until either a macro's exact pattern matches or the
inter-digit timeout elapses with no match -- the classic app_rpt convention
of '*' + node id + function digit sequences.

Macros are plain data (not a callable) so they can be created/edited through
the API/dashboard (JSON in, JSON out) rather than only at startup in Python.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional, get_args

from .events import CodedCommand, ControllerCommand, RunAction, SendLinkCommand

CODE_DIGITS = 6
CODE_TIMEOUT = 15.0  # seconds to key the code after the macro, as it's read off a phone

# "link" sends `command` to the network link layer; the rest run locally,
# with `command` as their argument where one is needed:
#   time          speak the time              announcement  play announcement <id>
#   weather       speak active NWS alerts     say           speak <text>
#   id            station ID now              parrot        play the next transmission back
#   tx_disable / tx_enable   turn the transmitter off/on (put a secret code in the pattern)
#   aprs          speak how many APRS stations are nearby, and the closest
#   gpio          switch a CM108 output: `<pin> on|off|toggle|pulse [seconds]`
#   lockout_clear end a stuck-carrier lockout now
#   net_start / net_end      start or end net mode (api.net)
#   link_radio_on / link_radio_off   connect or disconnect the link radio (api.link_radio)
#   homeassistant call a Home Assistant webhook `<id>`, or fire `event:<type>` (api.homeassistant)
MacroAction = Literal[
    "link", "time", "weather", "id", "announcement", "say", "parrot", "tx_disable", "tx_enable", "aprs", "gpio",
    "lockout_clear", "net_start", "net_end", "homeassistant", "link_radio_on", "link_radio_off",
]
MACRO_ACTIONS: tuple[str, ...] = get_args(MacroAction)
ACTIONS_NEEDING_ARGUMENT = frozenset({"link", "announcement", "say", "gpio", "homeassistant"})


@dataclass(frozen=True)
class Macro:
    pattern: str
    description: str
    command: str = ""
    node_id: str = ""
    action: MacroAction = "link"
    needs_code: bool = False  # followed by a one-time code from an authenticator app (api.control_codes)

    def build_command(self) -> ControllerCommand:
        if self.action == "link":
            return SendLinkCommand(node_id=self.node_id, command=self.command)
        return RunAction(action=self.action, argument=self.command, pattern=self.pattern)


class DTMFCommandDecoder:
    def __init__(self, macros: list[Macro], interdigit_timeout: float = 3.0) -> None:
        self._macros = list(macros)
        self._interdigit_timeout = interdigit_timeout
        self._buffer = ""
        self._last_digit_at: Optional[float] = None
        self._awaiting_code: Optional[Macro] = None

    def list_macros(self) -> list[Macro]:
        return list(self._macros)

    def set_macros(self, macros: list[Macro]) -> None:
        self._macros = list(macros)

    def _timed_out(self, now: float) -> bool:
        timeout = CODE_TIMEOUT if self._awaiting_code else self._interdigit_timeout
        return self._last_digit_at is not None and now - self._last_digit_at > timeout

    def handle_digit(self, digit: str, now: float) -> Optional[ControllerCommand]:
        if self._timed_out(now):
            self._buffer = ""
            self._awaiting_code = None
        self._last_digit_at = now
        self._buffer += digit

        if self._awaiting_code is not None:
            if not self._buffer.isdigit():
                self._buffer = ""
                self._awaiting_code = None
            elif len(self._buffer) == CODE_DIGITS:
                macro, code = self._awaiting_code, self._buffer
                self._buffer = ""
                self._awaiting_code = None
                self._last_digit_at = None
                return CodedCommand(command=macro.build_command(), pattern=macro.pattern, code=code)
            return None

        for macro in self._macros:
            if self._buffer == macro.pattern:
                self._buffer = ""
                if macro.needs_code:
                    self._awaiting_code = macro
                    return None
                self._last_digit_at = None
                return macro.build_command()

        if not any(macro.pattern.startswith(self._buffer) for macro in self._macros):
            self._buffer = ""
        return None

    def tick(self, now: float) -> None:
        if self._timed_out(now):
            self._buffer = ""
            self._awaiting_code = None
            self._last_digit_at = None
