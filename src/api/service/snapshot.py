"""Status snapshots and saved-state helpers."""
from __future__ import annotations

import dataclasses
import logging
from datetime import datetime
from typing import Optional

from controller.announcements import Announcement, announcement_from_dict
from controller.config import CONFIG_FIELDS, RepeaterConfig
from controller.macros import Macro
from playout.tts import spoken_time

_logger = logging.getLogger("moreopenrepeater.service")

LINK_AUDIO_NODE = "allstar"  # RemoteKeyed's node_id for the AllStar node's own transmitter
LINK_RADIO_NODE = "link-radio"  # ...and for the link radio hearing the far end (api.link_radio)

WEATHER_SUMMARY_MAX = 3
MAX_HELD_ANNOUNCEMENTS = 10

_CONFIG_FIELDS = CONFIG_FIELDS
_MACRO_FIELDS = {f.name for f in dataclasses.fields(Macro)}


def talking_clock_text(now: datetime) -> str:
    return f"The time is {spoken_time(now)}."


def _known_fields(data: dict, known: set[str], what: str) -> dict:
    """Saved state and backups may come from an older or newer version of
    this app; drop fields this version doesn't know instead of crashing, and
    let missing ones fall back to their defaults."""
    unknown = set(data) - known
    if unknown:
        _logger.warning("ignoring unknown %s fields: %s", what, sorted(unknown))
    return {k: v for k, v in data.items() if k in known}


def config_from_snapshot(data: dict) -> RepeaterConfig:
    config = dict(data.get("config", {}))
    if "public_page_enabled" in config:  # saved before public_page_mode
        config.setdefault("public_page_mode", "anyone" if config.pop("public_page_enabled") else "off")
    return RepeaterConfig.from_flat_dict(_known_fields(config, _CONFIG_FIELDS, "config"))


def macros_from_snapshot(data: dict) -> list[Macro]:
    return [Macro(**_known_fields(m, _MACRO_FIELDS, "macro")) for m in data.get("macros", [])]


def announcements_from_snapshot(data: dict) -> list[Announcement]:
    return [announcement_from_dict(a) for a in data.get("announcements", [])]


@dataclasses.dataclass(frozen=True)
class StatusSnapshot:
    state: str
    ptt_active: bool
    cos_active: bool
    ctcss_hz: Optional[float]
    linked_nodes: list[str]
    last_clip: Optional[str]
    timestamp: float
    transmitter_enabled: bool = True
    locked_out: bool = False
    net_active: bool = False
    gmrs_mode: bool = False
    callsign: str = ""
    tot_remaining: Optional[float] = None
    hang_remaining: Optional[float] = None
    id_due_in: Optional[float] = None
