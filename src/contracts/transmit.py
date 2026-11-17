"""Asking the core to transmit: the `speak.request` contract.

A module (weather alerts, for example) never keys the radio. It sends the core
a request that is expiring, prioritized and idempotent, and the core decides.
The core owns arbitration; this file only defines the messages and the two
rules every core must apply the same way: drop what has expired, and never
transmit the same idempotency key twice.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum, IntEnum
from typing import Literal, Optional

from pydantic import Field, model_validator

from .common import CODE_PATTERN, ID_PATTERN, Message, StructuredError, UtcDatetime, _to_utc

#: A request may not ask to wait longer than this. Anything that needs to be
#: said later should be re-sent later.
MAX_LIFETIME = timedelta(hours=1)
MAX_TEXT = 500
#: Wider than a message ID: a module may build the key from an upstream ID such
#: as an NWS alert ID, which contains dots and colons.
IDEMPOTENCY_KEY_PATTERN = r"^[A-Za-z0-9._:-]{8,128}$"


class Priority(IntEnum):
    LOW = 10
    NORMAL = 20
    HIGH = 30
    EMERGENCY = 40


class SpeakRequest(Message):
    """Please say this, if you can, before `deadline`."""

    kind: Literal["speak.request"] = "speak.request"
    #: Same key means same request. A retry reuses it; the core speaks it once.
    idempotency_key: str = Field(pattern=IDEMPOTENCY_KEY_PATTERN)
    #: What this is about, for arbitration and the dashboard (`weather.alert`).
    topic: str = Field(pattern=CODE_PATTERN, max_length=80)
    priority: Priority = Priority.NORMAL
    #: After this the core drops the request unspoken. Required.
    deadline: UtcDatetime
    #: Text to speak. Treated as message content: never logged by default.
    text: str = Field(min_length=1, max_length=MAX_TEXT)

    @model_validator(mode="after")
    def _deadline_is_in_range(self) -> "SpeakRequest":
        lifetime = self.deadline - self.header.emitted_at
        if lifetime <= timedelta(0):
            raise ValueError("deadline must be after the time the request was emitted")
        if lifetime > MAX_LIFETIME:
            raise ValueError(f"deadline may be at most {MAX_LIFETIME} after the request was emitted")
        return self

    def is_expired(self, now: datetime) -> bool:
        return _to_utc(now) >= self.deadline


class SpeakStatus(str, Enum):
    ACCEPTED = "accepted"  # queued for transmission
    DUPLICATE = "duplicate"  # this idempotency key was already accepted
    EXPIRED = "expired"  # the deadline passed before the core got to it
    REJECTED = "rejected"  # the core's policy said no (see `error`)


class SpeakDecision(Message):
    """The core's answer to a `SpeakRequest`. `header.request_id` is the
    request's `message_id`."""

    kind: Literal["speak.decision"] = "speak.decision"
    status: SpeakStatus
    #: The correlation ID every event for this transmission will carry.
    #: Only the core mints it.
    transmission_id: Optional[str] = Field(default=None, pattern=ID_PATTERN)
    error: Optional[StructuredError] = None

    @model_validator(mode="after")
    def _fields_match_status(self) -> "SpeakDecision":
        if self.header.request_id is None:
            raise ValueError("a decision must set header.request_id")
        if self.status is SpeakStatus.ACCEPTED and self.transmission_id is None:
            raise ValueError("an accepted request needs a transmission_id")
        if self.status is SpeakStatus.REJECTED and self.error is None:
            raise ValueError("a rejected request needs an error")
        return self


class IdempotencyLedger:
    """Remembers accepted keys until their deadline passes, in bounded memory.

    If it fills, it forgets the key closest to expiry. That can let one
    duplicate through after `max_entries` requests are live at once, which is
    the right trade for a repeater: bounded memory beats perfect dedup.
    """

    def __init__(self, max_entries: int = 1024) -> None:
        self.max_entries = max_entries
        self._deadlines: dict[str, datetime] = {}

    def __len__(self) -> int:
        return len(self._deadlines)

    def _prune(self, now: datetime) -> None:
        for key in [k for k, deadline in self._deadlines.items() if deadline <= now]:
            del self._deadlines[key]

    def record(self, key: str, deadline: datetime, now: datetime) -> bool:
        """Remember `key`. Returns False if it is already remembered."""
        now = _to_utc(now)
        self._prune(now)
        if key in self._deadlines:
            return False
        if len(self._deadlines) >= self.max_entries:
            del self._deadlines[min(self._deadlines, key=self._deadlines.__getitem__)]
        self._deadlines[key] = _to_utc(deadline)
        return True

    def forget(self, key: str) -> None:
        """Let `key` be sent again. For a request that was accepted for screening but
        then could not be queued, so a retry is not wrongly called a duplicate."""
        self._deadlines.pop(key, None)


def screen_speak_request(
    request: SpeakRequest, now: datetime, ledger: IdempotencyLedger
) -> tuple[SpeakStatus, Optional[StructuredError]]:
    """The two rules every core applies first, before its own policy.

    Expired requests are dropped without being remembered. Otherwise a repeated
    idempotency key is a duplicate. Anything else is acceptable.
    """
    if request.is_expired(now):
        return SpeakStatus.EXPIRED, StructuredError(
            code="speak.expired", message="The deadline passed before the request could be sent."
        )
    if not ledger.record(request.idempotency_key, request.deadline, now):
        return SpeakStatus.DUPLICATE, None
    return SpeakStatus.ACCEPTED, None
