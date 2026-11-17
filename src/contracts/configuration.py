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
