"""Pieces every contract message shares: header, errors and freshness."""
from __future__ import annotations

import itertools
import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, ClassVar, Optional

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from .redaction import redact

ID_PATTERN = r"^[A-Za-z0-9_-]{8,64}$"
CODE_PATTERN = r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$"
MODULE_PATTERN = r"^[a-z][a-z0-9_-]{0,31}$"


def new_id() -> str:
    """A fresh opaque ID for messages, requests and correlation."""
    return uuid.uuid4().hex


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _to_utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc)


UtcDatetime = Annotated[AwareDatetime, AfterValidator(_to_utc)]


class Sequencer:
    """Strictly increasing sequence numbers for one process.

    `boot_id` changes on every restart, so a consumer can tell "the counter
    went back to zero because the module restarted" from "events were lost".
    """

    def __init__(self) -> None:
        self.boot_id = new_id()
        self._counter = itertools.count()

    def next(self) -> int:
        return next(self._counter)


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    module: str = Field(pattern=MODULE_PATTERN)
    version: str = Field(min_length=1, max_length=32)


class Header(BaseModel):
    """Common header on commands, events, health, status and config messages."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(ge=1)
    message_id: str = Field(pattern=ID_PATTERN)
    emitted_at: UtcDatetime
    boot_id: str = Field(pattern=ID_PATTERN)
    seq: int = Field(ge=0)
    source: Source
    #: Follows one transmission (or one user action) across modules.
    correlation_id: Optional[str] = Field(default=None, pattern=ID_PATTERN)
    #: Set on a reply, to the `message_id` it answers.
    request_id: Optional[str] = Field(default=None, pattern=ID_PATTERN)


class StructuredError(BaseModel):
    """An error a module can return or log. `code` is stable and machine
    readable; `message` is for people and must not carry secrets or content."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str = Field(pattern=CODE_PATTERN)
    message: str = Field(max_length=300)
    retryable: bool = False
    #: Seconds to wait before retrying, when the sender knows.
    retry_after_s: Optional[float] = Field(default=None, ge=0)
    details: dict[str, Any] = Field(default_factory=dict)

    @field_validator("details")
    @classmethod
    def _redact_details(cls, value: dict[str, Any]) -> dict[str, Any]:
        # Redacted on the way in, so no sender can forget.
        return redact(value)


class Freshness(BaseModel):
    """How old a value is allowed to be before a consumer must stop trusting it.

    A stale GPS fix, a stale weather alert and a stale status all use this, so
    consumers handle "old data" one way.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    observed_at: UtcDatetime
    max_age_s: float = Field(gt=0)

    def age(self, now: datetime) -> timedelta:
        return _to_utc(now) - self.observed_at

    def is_stale(self, now: datetime) -> bool:
        return self.age(now).total_seconds() > self.max_age_s


class Message(BaseModel):
    """Base for every contract message: a header plus a body.

    Each message class pins its own `SCHEMA_VERSION`. A message whose header
    says a different version is rejected rather than half understood.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    SCHEMA_VERSION: ClassVar[int] = 1

    header: Header

    @model_validator(mode="after")
    def _header_matches_schema_version(self) -> "Message":
        if self.header.schema_version != self.SCHEMA_VERSION:
            raise ValueError(
                f"{type(self).__name__} is schema version {self.SCHEMA_VERSION}, header says {self.header.schema_version}"
            )
        return self
