"""Pydantic config models derived from controller.config facets.

ConfigResponse is generated from RepeaterConfig so the response/wire field set
cannot drift from the domain facets. ConfigUpdateRequest keeps per-field
validation constraints and clear_* flags (declared next to each facet via
CLEAR_FIELDS); see api.models.
"""
from __future__ import annotations

import dataclasses
from typing import Any, get_type_hints

from pydantic import BaseModel, ConfigDict, Field, create_model

from controller.config import RepeaterConfig
from controller.config.clear_fields import ALL_CLEAR_FIELDS


def _response_field_definitions() -> dict[str, Any]:
    """Map each RepeaterConfig field to a Pydantic field with the same default."""
    hints = get_type_hints(RepeaterConfig)
    defs: dict[str, Any] = {}
    for f in dataclasses.fields(RepeaterConfig):
        annotation = hints[f.name]
        if f.default_factory is not dataclasses.MISSING:
            defs[f.name] = (annotation, Field(default_factory=f.default_factory))
        elif f.default is not dataclasses.MISSING:
            defs[f.name] = (annotation, f.default)
        else:
            defs[f.name] = (annotation, ...)
    return defs


ConfigResponse = create_model(
    "ConfigResponse",
    __base__=BaseModel,
    __config__=ConfigDict(from_attributes=True),
    **_response_field_definitions(),
)
ConfigResponse.model_rebuild()


def clear_field_names() -> tuple[str, ...]:
    """Flat names that ConfigUpdateRequest may null via clear_<name>."""
    return ALL_CLEAR_FIELDS


def config_response_from_repeater(config: RepeaterConfig) -> ConfigResponse:
    return ConfigResponse.model_validate(config.to_flat_dict())


def repeater_from_config_response(response: ConfigResponse) -> RepeaterConfig:
    return RepeaterConfig.from_flat_dict(response.model_dump())
