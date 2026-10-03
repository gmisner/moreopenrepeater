"""When the link radio's transmitter keys (api.link_radio runs it).

The link radio carries what the repeater repeats to a far station (another
repeater's input, or a simplex link), and what it hears comes back as a
remote key-up. Its transmitter:

  - keys while the repeater repeats a local user, unless the link radio is
    receiving (it's usually simplex: the far end has the channel);
  - gives up after `timeout` seconds of one transmission (timeout tone),
    until that user unkeys;
  - sends a courtesy tone when the user unkeys, if wanted, then unkeys
    after a short hang;
  - identifies (47 CFR 97.119) within `id_interval` of the first
    transmission and every `id_interval` while it keeps transmitting --
    keying up by itself if it's idle, but never over the far end;
  - as a remote base, says when it's gone `idle_off` seconds without
    anyone using it, so it can be turned off.

No clocks or audio here: `update(now, sending, receiving)` returns what to do.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Union

HANG_SECONDS = 0.5
IDLE_ID_SECONDS = 20.0  # time for an owed ID to play before idling off


@dataclass(frozen=True)
class LinkPTT:
    active: bool


@dataclass(frozen=True)
class LinkPlay:
    clip: str


@dataclass(frozen=True)
class LinkIdle:
    """Nobody has used it for `idle_off` seconds (sent once)."""


LinkCommand = Union[LinkPTT, LinkPlay, LinkIdle]


@dataclass
class LinkRadioSettings:
    timeout: float = 180.0
    courtesy_tone: bool = True
    id_interval: float = 600.0
    idle_off: float = 0.0  # 0: never idle


class LinkRadioController:
    def __init__(self, settings: Optional[LinkRadioSettings] = None) -> None:
        self.settings = settings or LinkRadioSettings()
        self.ptt = False
        self.timed_out = False
        self._started_at = 0.0
        self._hang_until: Optional[float] = None
        self._id_due_at: Optional[float] = None  # None: no ID owed
        self._last_used: Optional[float] = None

    @property
    def id_owed(self) -> bool:
        return self._id_due_at is not None

    def update(self, now: float, sending: bool, receiving: bool) -> list[LinkCommand]:
        """`sending`: the repeater is repeating a local user. `receiving`:
        the link radio hears the far end."""
        commands: list[LinkCommand] = []
        if not sending:
            self.timed_out = False
        if sending and not receiving and not self.timed_out:
            if not self.ptt:
                self.ptt = True
                self._started_at = now
                commands.append(LinkPTT(True))
                if self._id_due_at is None:
                    self._id_due_at = now + self.settings.id_interval
            self._hang_until = None
            if now - self._started_at >= self.settings.timeout:
                self.timed_out = True
                self.ptt = False
                commands += [LinkPlay("timeout_tone"), LinkPTT(False)]
        elif self.ptt:
            if receiving:
                self._hang_until = now
            elif self._hang_until is None:
                self._hang_until = now + HANG_SECONDS
                if self.settings.courtesy_tone:
                    commands.append(LinkPlay("courtesy_tone"))
            if now >= self._hang_until:
                self.ptt = False
                self._hang_until = None
                commands.append(LinkPTT(False))
        if self._id_due_at is not None and now >= self._id_due_at and not receiving:
            # A clip keys the transmitter by itself until it finishes.
            commands.append(LinkPlay("id"))
            self._id_due_at = now + self.settings.id_interval if self.ptt else None
        if self._last_used is None or sending or receiving or self.ptt:
            self._last_used = now
        elif self.settings.idle_off and now - self._last_used >= self.settings.idle_off:
            if self._id_due_at is not None:
                commands.append(LinkPlay("id"))
                self._id_due_at = None
                self._last_used = now - self.settings.idle_off + IDLE_ID_SECONDS
            else:
                commands.append(LinkIdle())
                self._last_used = float("inf")
        return commands

    def reset(self) -> None:
        """The link radio stopped: nothing is keyed, and no ID is owed."""
        self.ptt = False
        self.timed_out = False
        self._hang_until = None
        self._id_due_at = None
        self._last_used = None
