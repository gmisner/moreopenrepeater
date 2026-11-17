"""The structured log event every module writes (one JSON object per line)."""
from __future__ import annotations

from enum import IntEnum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .common import CODE_PATTERN, ID_PATTERN, MODULE_PATTERN, UtcDatetime
from .redaction import redact

LOG_EVENT_SCHEMA_VERSION = 1


class Severity(IntEnum):
    """Numbers match Python's `logging` levels, so they sort and filter the same."""

    DEBUG = 10
    INFO = 20
    WARNING = 30
    ERROR = 40
    CRITICAL = 50


class LogEvent(BaseModel):
    """One thing that happened, in a form a collector can store and search.

    `event` is a stable dotted name (`ptt.commanded`, `ptt.feedback`,
    `tx.started`). Put variable data in `context`, not in `event` or `message`,
    so events can be counted and filtered. `context` is redacted on
    construction (see `contracts.redaction`).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(default=LOG_EVENT_SCHEMA_VERSION, ge=1)
    #: UTC wall-clock time. Do not use it for ordering; use `boot_id` + `seq`.
    time: UtcDatetime
    #: Order within one module process. Resets when `boot_id` changes.
    boot_id: str = Field(pattern=ID_PATTERN)
    seq: int = Field(ge=0)
    severity: Severity
    event: str = Field(pattern=CODE_PATTERN, max_length=80)
    module: str = Field(pattern=MODULE_PATTERN)
    module_version: str = Field(min_length=1, max_length=32)
    correlation_id: Optional[str] = Field(default=None, pattern=ID_PATTERN)
    error_code: Optional[str] = Field(default=None, pattern=CODE_PATTERN, max_length=80)
    #: Short human text. Static wording only: no secrets, no message content.
    message: Optional[str] = Field(default=None, max_length=200)
    context: dict[str, Any] = Field(default_factory=dict)

    @field_validator("context")
    @classmethod
    def _redact_context(cls, value: dict[str, Any]) -> dict[str, Any]:
        return redact(value)

    def to_json_line(self) -> str:
        return self.model_dump_json(exclude_none=True)
