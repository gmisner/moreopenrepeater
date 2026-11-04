"""Autopatch dial/hangup command application and call lifecycle."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional, Union

from controller.events import DialPatch, HangupPatch
from playout.renderer import TTS_PREFIX

from .protocols import PatchCalls

if TYPE_CHECKING:
    from .facade import RepeaterService

_logger = logging.getLogger("moreopenrepeater.service")


class PatchSupport:
    """Applies controller patch commands and tracks the live autopatch."""

    def __init__(self, host: "RepeaterService") -> None:
        self._host = host
        self.calls: Optional[PatchCalls] = None

    def run_command(self, command: Union[DialPatch, HangupPatch]) -> None:
        if isinstance(command, HangupPatch):
            if self.calls is not None:
                self.calls.hangup("hung up by a user")
            return
        _logger.info("autopatch dial requested over DTMF")
        if self.calls is None:
            self._host.speak(TTS_PREFIX + "Autopatch is not available.")
            return
        self.calls.dial(command.number, "DTMF")

    def begin(self) -> None:
        """An autopatch call started: hold the transmitter up for it."""
        host = self._host
        host.controller.set_patch_call_active(True)
        host._apply_commands(host.controller.start_patch(host._clock()))

    def end(self) -> None:
        host = self._host
        host.controller.set_patch_call_active(False)
        host._apply_commands(host.controller.end_patch(host._clock()))

    def pass_digit(self, digit: str) -> None:
        """The audio's DTMF is muted, so a phone menu on an autopatch call gets the digit this way."""
        if self.calls is not None:
            self.calls.send_digit(digit)
