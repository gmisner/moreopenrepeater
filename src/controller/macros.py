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

from .events import ControllerCommand, RunAction, SendLinkCommand

# "link" sends `command` to the network link layer; the rest run locally,
# with `command` as their argument where one is needed:
#   time          speak the time              announcement  play announcement <id>
#   weather       speak active NWS alerts     say           speak <text>
#   id            station ID now              parrot        play the next transmission back
#   tx_disable / tx_enable   turn the transmitter off/on (put a secret code in the pattern)
#   aprs          speak how many APRS stations are nearby, and the closest
#   gpio          switch a CM108 output: `<pin> on|off|toggle|pulse [seconds]`
MacroAction = Literal[
    "link", "time", "weather", "id", "announcement", "say", "parrot", "tx_disable", "tx_enable", "aprs", "gpio"
]
MACRO_ACTIONS: tuple[str, ...] = get_args(MacroAction)
ACTIONS_NEEDING_ARGUMENT = frozenset({"link", "announcement", "say", "gpio"})


@dataclass(frozen=True)
class Macro:
    pattern: str
    description: str
    command: str = ""
    node_id: str = ""
    action: MacroAction = "link"

    def build_command(self) -> ControllerCommand:
        if self.action == "link":
            return SendLinkCommand(node_id=self.node_id, command=self.command)
        return RunAction(action=self.action, argument=self.command)


class DTMFCommandDecoder:
    def __init__(self, macros: list[Macro], interdigit_timeout: float = 3.0) -> None:
        self._macros = list(macros)
        self._interdigit_timeout = interdigit_timeout
        self._buffer = ""
        self._last_digit_at: Optional[float] = None

    def list_macros(self) -> list[Macro]:
        return list(self._macros)

    def set_macros(self, macros: list[Macro]) -> None:
        self._macros = list(macros)

    def handle_digit(self, digit: str, now: float) -> Optional[ControllerCommand]:
        if self._last_digit_at is not None and now - self._last_digit_at > self._interdigit_timeout:
            self._buffer = ""
        self._last_digit_at = now
        self._buffer += digit

        for macro in self._macros:
            if self._buffer == macro.pattern:
                command = macro.build_command()
                self._buffer = ""
                self._last_digit_at = None
                return command

        if not any(macro.pattern.startswith(self._buffer) for macro in self._macros):
            self._buffer = ""
        return None

    def tick(self, now: float) -> None:
        if self._last_digit_at is not None and now - self._last_digit_at > self._interdigit_timeout:
            self._buffer = ""
            self._last_digit_at = None
