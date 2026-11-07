"""Macros, announcements, and snapshot API models."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from controller.macros import ACTIONS_NEEDING_ARGUMENT, KEYED_ENTRY_ACTIONS, MacroAction

from ..gpio import parse_gpio_command
from .config import ConfigResponse
from .types import HOMEASSISTANT_TARGET, MAX_ANNOUNCEMENT_LENGTH, hh_mm, weekdays

class MacroResponse(BaseModel):
    pattern: str
    description: str
    command: str
    node_id: str
    action: MacroAction
    needs_code: bool
    help_hidden: Optional[bool] = None  # left out of spoken help; /api/macros resolves None to the default


class MacroCreateRequest(BaseModel):
    pattern: str = Field(min_length=1, pattern=r"^[0-9A-D*#]+$")
    description: str = ""
    command: str = ""
    node_id: str = ""
    action: MacroAction = "link"
    needs_code: bool = False
    help_hidden: Optional[bool] = None  # None: hidden if it's a sensitive action or needs a code

    @model_validator(mode="after")
    def _command_when_needed(self) -> "MacroCreateRequest":
        if self.action in ACTIONS_NEEDING_ARGUMENT and not self.command.strip():
            raise ValueError(f"a {self.action!r} macro needs a command")
        if self.action == "help" and not re.fullmatch(r"[0-9]{0,2}", self.command.strip()):
            raise ValueError("a help macro's command is an optional page number")
        if self.action == "gpio":
            parse_gpio_command(self.command)
        if self.action == "homeassistant" and not re.fullmatch(HOMEASSISTANT_TARGET, self.command.strip()):
            raise ValueError("a Home Assistant macro needs a webhook ID or event:<type> (letters, digits, _ . -)")
        if self.action == "metar":
            self.command = self.command.strip().upper()
            if not re.fullmatch(r"[A-Z0-9]{4}|", self.command):
                raise ValueError("an airport weather macro needs a 4-letter ICAO code, or none for every listed airport")
        if self.action == "sensor":
            self.command = self.command.strip().lower()
            if not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}|", self.command):
                raise ValueError("a sensor macro needs a sensor id, or none for every configured sensor")
        if self.action == "remote_shift" and self.command.strip() not in ("plus", "minus", "simplex"):
            raise ValueError("a remote base shift macro's command is plus, minus or simplex")
        if self.action in KEYED_ENTRY_ACTIONS and self.needs_code:
            raise ValueError("a macro that takes keyed digits can't also need a one-time code")
        return self


class AnnouncementFields(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    message: str = Field(default="", max_length=MAX_ANNOUNCEMENT_LENGTH)
    asset_id: Optional[str] = None
    kind: Literal["interval", "weekly"] = "interval"
    every_minutes: int = Field(default=60, ge=1, le=7 * 24 * 60)
    times: list[str] = []
    days: list[int] = [0, 1, 2, 3, 4, 5, 6]
    enabled: bool = True
    on_linked_radio: bool = False

    @field_validator("times")
    @classmethod
    def _valid_times(cls, times: list[str]) -> list[str]:
        return sorted({hh_mm(value) for value in times})

    @field_validator("days")
    @classmethod
    def _valid_days(cls, days: list[int]) -> list[int]:
        return weekdays(days)

    @model_validator(mode="after")
    def _has_something_to_say(self) -> "AnnouncementFields":
        if not self.message.strip() and not self.asset_id:
            raise ValueError("an announcement needs a message or an audio clip")
        if self.kind == "weekly" and not self.times:
            raise ValueError("a weekly announcement needs at least one time")
        return self


class AnnouncementModel(AnnouncementFields):
    id: str = Field(min_length=1)


class AnnouncementResponse(AnnouncementModel):
    next_run: Optional[datetime]


class SnapshotModel(BaseModel):
    config: ConfigResponse
    macros: list[MacroResponse]
    announcements: list[AnnouncementModel] = []


class SnapshotImportRequest(BaseModel):
    """A backup file. `config` is loosely typed so a backup from an older
    version (missing newer settings) still restores -- missing fields get
    defaults, then the merged result is validated as a ConfigResponse."""

    config: dict[str, Any]
    macros: list[MacroCreateRequest] = []
    announcements: list[AnnouncementModel] = []
