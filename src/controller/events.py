"""Plain-data events/commands shared across package boundaries.

The state machine only ever sees these types -- never sounddevice, hid, or
network client objects -- so it can be driven and tested with synthetic
event sequences alone.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Union


@dataclass(frozen=True)
class COSChanged:
    """Local carrier-operated squelch state changed."""

    active: bool


@dataclass(frozen=True)
class CTCSSChanged:
    """The currently-detected sub-audible tone changed (None = no tone)."""

    tone_hz: Optional[float]


@dataclass(frozen=True)
class DTMFDigit:
    """A single DTMF digit was decoded from the receive audio."""

    digit: str


@dataclass(frozen=True)
class RemoteKeyed:
    """A linked node's transmitter keyed up or down."""

    node_id: str
    keyed: bool


@dataclass(frozen=True)
class LinkStateChanged:
    """A link to a remote node connected or disconnected."""

    node_id: str
    linked: bool


ControllerEvent = Union[COSChanged, CTCSSChanged, DTMFDigit, RemoteKeyed, LinkStateChanged]


@dataclass(frozen=True)
class AssertPTT:
    """Key (or unkey) the local transmitter."""

    active: bool


@dataclass(frozen=True)
class PlayAudio:
    """Play a named audio clip (e.g. courtesy tone, timeout tone, station ID)."""

    clip: str


@dataclass(frozen=True)
class SendLinkCommand:
    """Send a command to the network link layer (e.g. connect/disconnect a node)."""

    node_id: str
    command: str


@dataclass(frozen=True)
class RunAction:
    """A local repeater function triggered by a DTMF macro (talking clock,
    transmitter disable, ...). The service layer carries it out."""

    action: str
    argument: str = ""
    pattern: str = ""  # the macro that ran it


@dataclass(frozen=True)
class DialPatch:
    """Place an autopatch phone call (digits only, not yet checked against the allowed numbers)."""

    number: str


@dataclass(frozen=True)
class HangupPatch:
    """End the autopatch call in progress."""


@dataclass(frozen=True)
class CodedCommand:
    """A macro that needs a one-time code, with the code keyed after it. The
    service layer checks the code before carrying out `command`."""

    command: Union[SendLinkCommand, RunAction]
    pattern: str
    code: str


ControllerCommand = Union[AssertPTT, PlayAudio, SendLinkCommand, RunAction, DialPatch, HangupPatch, CodedCommand]
