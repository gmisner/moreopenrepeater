"""Small commands the API sends to the core, and the core's answer.

Stage 3 slice 7c-4a (#229). Like `ConfigChange`, these are for the API's trusted channel
(the core's control socket, protected by file permissions) and are not for modules. A
module that wants the repeater to say something sends a `SpeakRequest`.

The commands are the dashboard's one-shot actions: clear a lockout, start or end net
mode, and the simulator. Each carries only the parameters it needs, checked here and
again by the core. The simulator can key the transmitter (a simulated COS makes the
repeater transmit), so the core refuses it unless it was started with simulation
allowed.
"""
from __future__ import annotations

import math
import re
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import Field, model_validator

from .common import Message, StructuredError


class CommandName(str, Enum):
    CLEAR_LOCKOUT = "clear_lockout"
    SET_NET_ACTIVE = "set_net_active"
    SIMULATE_COS = "simulate_cos"
    SIMULATE_CTCSS = "simulate_ctcss"
    SIMULATE_DTMF = "simulate_dtmf"
    SIMULATE_REMOTE_KEYED = "simulate_remote_keyed"
    ADD_MACRO = "add_macro"
    DELETE_MACRO = "delete_macro"
    SAVE_ANNOUNCEMENT = "save_announcement"
    DELETE_ANNOUNCEMENT = "delete_announcement"
    QUEUE_CLIP = "queue_clip"
    IMPORT_SNAPSHOT = "import_snapshot"
    GPIO_SET_OUTPUT = "gpio_set_output"
    REMOTE_BASE_RETUNE = "remote_base_retune"
    AUTOPATCH_DIAL = "autopatch_dial"
    AUTOPATCH_HANGUP = "autopatch_hangup"
    ARM_CAPTURE = "arm_capture"
    LINK_EVENT = "link_event"
    LINK_SYNC = "link_sync"


#: The commands that make the controller act on an event that did not come from the radio.
SIMULATED = frozenset(
    {CommandName.SIMULATE_COS, CommandName.SIMULATE_CTCSS, CommandName.SIMULATE_DTMF, CommandName.SIMULATE_REMOTE_KEYED}
)

#: The commands that change the macros or the scheduled announcements.
PROGRAMMING = frozenset(
    {CommandName.ADD_MACRO, CommandName.DELETE_MACRO, CommandName.SAVE_ANNOUNCEMENT, CommandName.DELETE_ANNOUNCEMENT}
)

#: The commands that act on the radio side's hardware: an output pin, the remote base, a phone call,
#: recording the next transmission.
HARDWARE = frozenset(
    {
        CommandName.GPIO_SET_OUTPUT, CommandName.REMOTE_BASE_RETUNE, CommandName.AUTOPATCH_DIAL,
        CommandName.AUTOPATCH_HANGUP, CommandName.ARM_CAPTURE,
    }
)

#: What the AllStar link service tells the core about the network links: one thing that changed, or the whole
#: current picture. They carry what app_rpt reported, never anything the core cannot check, and are applied
#: even on a core that refuses the simulator (they are real events from a real node, not simulated ones).
LINK = frozenset({CommandName.LINK_EVENT, CommandName.LINK_SYNC})
LINK_EVENTS = ("connected", "disconnected", "keyed", "unkeyed")
MAX_LINK_NODES = 64

_DTMF = re.compile(r"^[0-9A-D*#]$")
_NODE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
#: The longest clip name the core takes: "tts:" and up to 2000 characters of text, with room.
MAX_CLIP_CHARS = 2100
_DIGITS = re.compile(r"^[0-9]{1,15}$")
_PATTERN = re.compile(r"^[0-9A-D*#]{1,32}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MAX_OBJECT_KEYS = 32
MAX_CAPTURE_SECONDS = 600  # the longest message the core will record

#: command -> parameter name -> the kind of value it must be
_PARAMS: dict[CommandName, dict[str, str]] = {
    CommandName.CLEAR_LOCKOUT: {},
    CommandName.SET_NET_ACTIVE: {"active": "bool"},
    CommandName.SIMULATE_COS: {"active": "bool"},
    CommandName.SIMULATE_CTCSS: {"tone_hz": "tone"},
    CommandName.SIMULATE_DTMF: {"digit": "digit"},
    CommandName.SIMULATE_REMOTE_KEYED: {"node_id": "node", "keyed": "bool"},
    # The contract only checks the shape. The core validates a macro or an announcement with
    # the same models the API's routes use (api.models.programming).
    CommandName.ADD_MACRO: {"macro": "object"},
    CommandName.DELETE_MACRO: {"pattern": "pattern"},
    CommandName.SAVE_ANNOUNCEMENT: {"announcement": "object"},
    CommandName.DELETE_ANNOUNCEMENT: {"announcement_id": "identifier"},
    # Render a clip (a recording, or text to speech, or "id") and queue it to play. The
    # clip name is content: it can be the text of an announcement, so it is never logged.
    CommandName.QUEUE_CLIP: {"clip": "clip", "on_linked_radio": "bool"},
    # Replace the whole configuration, macros and announcements (a restore). The core checks
    # it with the same validation the dashboard's restore uses. It carries credentials.
    CommandName.IMPORT_SNAPSHOT: {"snapshot": "object"},
    # The core checks these against its own configuration too (an output must be set up as one, a number
    # must be allowed, the transmitter must be on).
    CommandName.GPIO_SET_OUTPUT: {"pin": "pin", "on": "bool"},
    CommandName.REMOTE_BASE_RETUNE: {},
    CommandName.AUTOPATCH_DIAL: {"number": "digits", "actor": "text"},
    CommandName.AUTOPATCH_HANGUP: {"reason": "text"},
    # Record the next transmission instead of repeating it, for at most this many seconds. The core
    # hands the audio to the client that asked, as a file it names in a `capture` event.
    CommandName.ARM_CAPTURE: {"max_seconds": "seconds"},
    # One link changed: a node connected or disconnected, or started or stopped transmitting to us.
    CommandName.LINK_EVENT: {"node_id": "node", "event": "link_event"},
    # Everything the link service knows right now, {node: keyed}. The core makes its picture match: a node
    # it holds that is missing here is gone (and unkeyed), which also clears a node that was keyed when the
    # connection to app_rpt was lost.
    CommandName.LINK_SYNC: {"nodes": "link_nodes"},
}


def _valid(kind: str, value: Any) -> bool:
    if kind == "bool":
        return isinstance(value, bool)
    if kind == "tone":  # a CTCSS tone, or None for "no tone"
        return value is None or (
            isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and 0 < value <= 400
        )
    if kind == "digit":
        return isinstance(value, str) and bool(_DTMF.match(value))
    if kind == "node":
        return isinstance(value, str) and bool(_NODE.match(value))
    if kind == "clip":
        return isinstance(value, str) and 0 < len(value) <= MAX_CLIP_CHARS and "\x00" not in value
    if kind == "seconds":  # a recording length: a real number, not a bool, within what one can be
        return (
            isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
            and 1 <= value <= MAX_CAPTURE_SECONDS
        )
    if kind == "pin":
        return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 64
    if kind == "digits":
        return isinstance(value, str) and bool(_DIGITS.match(value))
    if kind == "text":  # a short label (who asked, why): no control characters
        return isinstance(value, str) and 0 < len(value) <= 200 and value.isprintable()
    if kind == "pattern":
        return isinstance(value, str) and bool(_PATTERN.match(value))
    if kind == "identifier":
        return isinstance(value, str) and bool(_IDENTIFIER.match(value))
    if kind == "link_event":
        return value in LINK_EVENTS
    if kind == "link_nodes":
        return (
            isinstance(value, dict) and len(value) <= MAX_LINK_NODES
            and all(isinstance(k, str) and bool(_NODE.match(k)) and isinstance(v, bool) for k, v in value.items())
        )
    if kind == "object":
        return isinstance(value, dict) and len(value) <= MAX_OBJECT_KEYS and all(isinstance(k, str) for k in value)
    return False


class CoreCommand(Message):
    kind: Literal["core.command"] = "core.command"
    command: CommandName
    params: dict[str, Any] = Field(default_factory=dict, max_length=4)

    @model_validator(mode="after")
    def _params_match_command(self) -> "CoreCommand":
        expected = _PARAMS[self.command]
        if set(self.params) != set(expected):
            raise ValueError(f"{self.command.value} takes exactly: {', '.join(sorted(expected)) or 'no parameters'}")
        for name, kind in expected.items():
            if not _valid(kind, self.params[name]):
                raise ValueError(f"{self.command.value}: bad value for {name}")
        return self


class CommandStatus(str, Enum):
    APPLIED = "applied"
    REJECTED = "rejected"  # nothing was done


class CommandResult(Message):
    """The core's answer to a `CoreCommand` (`header.request_id` points at it)."""

    kind: Literal["core.command.result"] = "core.command.result"
    status: CommandStatus
    error: Optional[StructuredError] = None

    @model_validator(mode="after")
    def _fields_match_status(self) -> "CommandResult":
        if self.header.request_id is None:
            raise ValueError("a command result must set header.request_id")
        if self.status is CommandStatus.REJECTED and self.error is None:
            raise ValueError("a rejected command needs an error")
        return self
