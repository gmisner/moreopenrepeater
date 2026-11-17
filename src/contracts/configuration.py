"""Configuration delivered to a module, and its acknowledgement.

The core starts from its own local copy and never waits for this. Messages
carry no secrets: a secret goes to a module by name (`secret_refs`) and is
read from a Docker secret or environment file, so it cannot end up in a log,
a bus message or a support bundle.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Optional

from pydantic import Field, field_validator, model_validator

from .common import MODULE_PATTERN, Message, StructuredError
from .redaction import find_sensitive_keys


class ConfigSnapshot(Message):
    kind: Literal["config.snapshot"] = "config.snapshot"
    #: The module this is for.
    module: str = Field(pattern=MODULE_PATTERN)
    #: Increases with every change. A module ignores a version it has already applied.
    config_version: int = Field(ge=1)
    values: dict[str, Any]
    #: Names of secrets the module must read from its secret store.
    secret_refs: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("values")
    @classmethod
    def _no_secrets(cls, value: dict[str, Any]) -> dict[str, Any]:
        found = find_sensitive_keys(value)
        if found:
            raise ValueError(f"config values must not carry secrets, use secret_refs: {', '.join(found)}")
        return value


class ConfigResult(str, Enum):
    APPLIED = "applied"
    RESTART_REQUIRED = "restart_required"  # accepted, takes effect on restart
    REJECTED = "rejected"  # the module kept its previous config


class ConfigApplied(Message):
    """A module's answer to a `ConfigSnapshot` (`header.request_id` points at it)."""

    kind: Literal["config.applied"] = "config.applied"
    config_version: int = Field(ge=1)
    result: ConfigResult
    error: Optional[StructuredError] = None

    @model_validator(mode="after")
    def _fields_match_result(self) -> "ConfigApplied":
        if self.header.request_id is None:
            raise ValueError("a config acknowledgement must set header.request_id")
        if self.result is ConfigResult.REJECTED and self.error is None:
            raise ValueError("a rejected config needs an error")
        return self


# -- The trusted channel between the API and the core -------------------------------------
#
# `ConfigSnapshot` goes to modules and refuses secrets. These two go over the core's
# control socket to the API, which is the same machine and the same user group, and the
# API must be able to read and change the WHOLE configuration, the autopatch PIN and the
# other credentials included. They are not for modules and must never be sent to one.
# Nothing may log `values`.


class ConfigState(Message):
    """The core's whole saved configuration, as the API reads it (`GET /config`)."""

    kind: Literal["config.state"] = "config.state"
    #: Starts at 1 when the core starts and rises with every change to its configuration.
    config_version: int = Field(ge=1)
    #: Every setting by its flat name, secrets included. Never logged.
    values: dict[str, Any] = Field(repr=False)


class ConfigChange(Message):
    """The API asks the core to change some settings (`POST /config`). The core checks the
    change itself and answers with a `ConfigApplied` whose `header.request_id` is this
    message's `header.message_id`."""

    kind: Literal["config.change"] = "config.change"
    #: Only the settings to change, by flat name. Never logged.
    values: dict[str, Any] = Field(min_length=1, max_length=400, repr=False)
    #: When set, the change is refused unless the core is still at this version, so two
    #: writers cannot silently overwrite each other.
    expected_version: Optional[int] = Field(default=None, ge=1)


class ProgrammingState(Message):
    """The macros and scheduled announcements the core holds (`GET /programming`). For the
    API's trusted channel, like `ConfigState`. An announcement carries `next_run` (an ISO
    time or null), which only the core's scheduler knows."""

    kind: Literal["programming.state"] = "programming.state"
    #: The same number `config.state` carries: it rises when either changes.
    version: int = Field(ge=1)
    macros: list[dict[str, Any]] = Field(max_length=1000)
    announcements: list[dict[str, Any]] = Field(max_length=1000)


class LinkEvents(Message):
    """Recent link activity (connects, disconnects, keying) for the dashboard, newest first
    (`GET /link-events`). For the API's trusted channel."""

    kind: Literal["link.events"] = "link.events"
    events: list[dict[str, Any]] = Field(max_length=500)


class CoreLogs(Message):
    """Recent lines from the core's own log (`GET /logs?after=<cursor>`), oldest first, so the
    dashboard's Logs page shows the core and the API together. For the API's trusted channel.
    A `cursor` from before the core restarted means nothing to the new run: `header.boot_id`
    changes, and the caller starts again from 0."""

    kind: Literal["core.logs"] = "core.logs"
    #: Ask for lines after this next time.
    cursor: int = Field(ge=0)
    #: Each `{"seq": int, "line": str, "level": str, "at": float}`.
    lines: list[dict[str, Any]] = Field(max_length=1000)


class CoreHardware(Message):
    """What the core's own hardware reports: audio engine, GPIO, fan, link radio, remote base,
    monitor receiver, autopatch, AllStar node and DMR (`GET /hardware`), each its status as a plain
    object. For the API's trusted channel, so the dashboard's pages about them work with the core in
    another process. A part that could not be read in time is listed in `unavailable`, not guessed."""

    kind: Literal["core.hardware"] = "core.hardware"
    statuses: dict[str, dict[str, Any]]
    unavailable: list[str] = Field(default_factory=list, max_length=32)
