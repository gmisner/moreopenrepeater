"""Health reports: what a module says about itself, on request or on a timer."""
from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .common import CODE_PATTERN, Message, StructuredError


class HealthState(str, Enum):
    STARTING = "starting"  # not ready for work yet
    OK = "ok"
    DEGRADED = "degraded"  # working, but something is wrong
    FAILED = "failed"  # not doing its job


_SEVERITY = {HealthState.OK: 0, HealthState.DEGRADED: 1, HealthState.FAILED: 2}


class HealthCheck(BaseModel):
    """One thing a module checked: its device, its upstream service, its queue."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=CODE_PATTERN, max_length=60)
    state: Literal[HealthState.OK, HealthState.DEGRADED, HealthState.FAILED]
    error: Optional[StructuredError] = None


class HealthReport(Message):
    """A module's overall state plus the checks behind it.

    The overall state may not look better than its worst check, so a dashboard
    can trust the summary. `starting` is exempt: checks may fail while a
    module is still coming up.
    """

    kind: Literal["health.report"] = "health.report"
    state: HealthState
    uptime_s: float = Field(ge=0)
    checks: list[HealthCheck] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def _summary_is_not_rosier_than_the_checks(self) -> "HealthReport":
        if self.state is HealthState.STARTING:
            return self
        worst = max((_SEVERITY[check.state] for check in self.checks), default=0)
        if _SEVERITY[self.state] < worst:
            raise ValueError("overall state is better than its worst check")
        return self
