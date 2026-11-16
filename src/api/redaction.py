"""Keep access codes out of logs, audit records and responses for roles that don't need them.

The incoming autopatch PIN is an access control. Read-only accounts and the API
tokens handed to helpers can read the dashboard's config and logs, so the PIN is
shown only to admins and never written into a log line. This is the small,
explicit version; a shared filter for every log call is planned (see #228).
"""
from __future__ import annotations

from typing import Mapping

REDACTED = "[hidden]"
SENSITIVE_CONFIG_FIELDS = frozenset({"autopatch_incoming_pin"})


def shown(key: str, value: object) -> object:
    """The value to put in a log line or audit detail. Empty values stay visible."""
    return REDACTED if key in SENSITIVE_CONFIG_FIELDS and value else value


def redact_overrides(values: Mapping[str, object]) -> dict[str, object]:
    return {key: shown(key, value) for key, value in values.items()}


def redact_config_for(config, role: str):
    """A copy of a config response with access codes hidden unless the viewer is an admin."""
    if role == "admin":
        return config
    hidden = {name: REDACTED for name in SENSITIVE_CONFIG_FIELDS if getattr(config, name, None)}
    return config.model_copy(update=hidden) if hidden else config
