"""Checking a settings snapshot before it replaces everything (restore and import).

This is the validation `api.app` has always done, moved here so the core can run the very
same check on a snapshot that arrives over its control socket (Stage 3 slice 7c-5, #229).
It imports no web framework.
"""
from __future__ import annotations

from typing import Callable

from controller.config import RepeaterConfig

from .models import ConfigResponse, SnapshotImportRequest


def validate_config_values(values: dict) -> RepeaterConfig:
    """Type-check a full config dict through the API model. Raises pydantic's `ValidationError`."""
    validated = ConfigResponse.model_validate({k: v for k, v in values.items() if k in ConfigResponse.model_fields})
    return RepeaterConfig.from_flat_dict(validated.model_dump())


def snapshot_to_import(
    snapshot: SnapshotImportRequest, validate: Callable[[dict], RepeaterConfig] = validate_config_values
) -> dict:
    """The flat `{config, macros, announcements}` dict a service imports. Settings the snapshot
    lacks (a backup from an older version) get their defaults."""
    config = validate({**RepeaterConfig().to_flat_dict(), **snapshot.config})
    return {
        "config": config.to_flat_dict(),
        "macros": [m.model_dump() for m in snapshot.macros],
        "announcements": [a.model_dump() for a in snapshot.announcements],
    }
