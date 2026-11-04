"""Backup, updates, Tailscale, alerts, logs, and restart API models."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from .types import UpdateChannel, _ONE_LINE, _URL_OR_EMPTY, hh_mm, weekdays

class SavedBackupResponse(BaseModel):
    name: str
    created_at: datetime
    size: int
    contents: dict[str, int]


class BackupFolderResponse(BaseModel):
    enabled: bool
    directory: Optional[str]
    last_error: Optional[str]
    backups: list[SavedBackupResponse]


class BackupRestoreResponse(BaseModel):
    macros: int
    announcements: int
    users: Optional[int]  # None: the backup had no accounts, so the current ones were kept
    audio_clips: int
    history: bool
    recordings: int
    hub_sites: Optional[int] = None  # None: backup had no hub-sites.json (or hub not persisted)

class CommitSummary(BaseModel):
    sha: str
    date: str
    subject: str


class UpdateRunStatus(BaseModel):
    state: Literal["requested", "running", "succeeded", "failed", "rolled_back"]
    channel: Optional[str] = None
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    from_sha: Optional[str] = None
    to_sha: Optional[str] = None
    message: Optional[str] = None


class AutoUpdateSettings(BaseModel):
    enabled: bool
    days: list[int] = Field(min_length=1)
    start: str
    end: str  # before start = the window runs past midnight
    idle_minutes: int = Field(ge=1, le=240)

    @field_validator("start", "end")
    @classmethod
    def _valid_time(cls, value: str) -> str:
        return hh_mm(value)

    @field_validator("days")
    @classmethod
    def _valid_days(cls, days: list[int]) -> list[int]:
        return weekdays(days)

    @model_validator(mode="after")
    def _window_has_length(self) -> "AutoUpdateSettings":
        if self.start == self.end:
            raise ValueError("the window needs different start and end times")
        return self


class AutoUpdateStatus(AutoUpdateSettings):
    last_check_at: Optional[float] = None
    last_result: Optional[str] = None
    next_window: Optional[datetime] = None


class UpdatesResponse(BaseModel):
    available: bool  # installed with install-pi.sh, so the updater units exist
    channel: UpdateChannel
    version: Optional[CommitSummary]
    status: Optional[UpdateRunStatus]
    auto: AutoUpdateStatus


class UpdateCheckResponse(BaseModel):
    channel: UpdateChannel
    branch: str
    latest: Optional[CommitSummary]
    relation: Optional[Literal["identical", "ahead", "behind", "diverged"]]
    new_commits: list[CommitSummary]
    new_commit_count: int = 0
    error: Optional[str]


class UpdateRequest(BaseModel):
    channel: UpdateChannel


class TailscaleRunStatus(BaseModel):
    state: Literal["requested", "running", "needs_login", "succeeded", "failed"]
    action: Optional[str] = None
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    message: Optional[str] = None
    auth_url: Optional[str] = None


class TailscaleResponse(BaseModel):
    available: bool  # install-pi.sh units present
    installed: bool
    backend_state: Optional[str] = None
    dns_name: Optional[str] = None
    auth_url: Optional[str] = None
    serve_enabled: bool
    serve_url: Optional[str] = None
    port: int
    cli_error: Optional[str] = None
    status: Optional[TailscaleRunStatus] = None


class TailscaleUpRequest(BaseModel):
    auth_key: Optional[str] = Field(default=None, max_length=200)


class TailscaleServeRequest(BaseModel):
    enabled: bool

class AlertSettingsResponse(BaseModel):
    """Everything but the secrets, which are only ever written."""

    enabled: bool
    ntfy_url: str
    telegram_chat_id: str
    email_to: str
    email_from: str
    smtp_host: str
    smtp_port: int
    smtp_security: Literal["starttls", "ssl", "none"]
    smtp_username: str
    temperature_limit_c: float


class AlertSettingsRequest(BaseModel):
    """Omitted fields are left as they are; an empty string clears one."""

    enabled: Optional[bool] = None
    ntfy_url: Optional[str] = Field(default=None, max_length=300, pattern=_URL_OR_EMPTY)
    ntfy_token: Optional[str] = Field(default=None, max_length=200, pattern=_ONE_LINE)
    telegram_token: Optional[str] = Field(default=None, max_length=200, pattern=r"^$|^[0-9]+:[A-Za-z0-9_-]+$")
    telegram_chat_id: Optional[str] = Field(default=None, max_length=64, pattern=r"^$|^-?[0-9]+$|^@[A-Za-z0-9_]+$")
    email_to: Optional[str] = Field(default=None, max_length=300, pattern=_ONE_LINE)
    email_from: Optional[str] = Field(default=None, max_length=200, pattern=_ONE_LINE)
    smtp_host: Optional[str] = Field(default=None, max_length=200, pattern=r"^[A-Za-z0-9.-]*$")
    smtp_port: Optional[int] = Field(default=None, ge=1, le=65535)
    smtp_security: Optional[Literal["starttls", "ssl", "none"]] = None
    smtp_username: Optional[str] = Field(default=None, max_length=200, pattern=_ONE_LINE)
    smtp_password: Optional[str] = Field(default=None, max_length=200)
    webhook_url: Optional[str] = Field(default=None, max_length=500, pattern=_URL_OR_EMPTY)
    temperature_limit_c: Optional[float] = Field(default=None, ge=40, le=100)


class SentAlert(BaseModel):
    at: datetime
    key: str
    title: str
    message: str
    severity: Literal["info", "warning", "critical"]
    sent: list[str]
    errors: dict[str, str]
    note: str


class AlertsResponse(BaseModel):
    settings: AlertSettingsResponse
    secrets_set: dict[str, bool]  # which write-only fields have a value
    channels: list[str]  # the ones set up well enough to send
    recent: list[SentAlert]
    system: dict[str, Any]  # api.health.SystemProbe.readings()
    last_unexpected_stop: Optional[dict[str, Any]]


class AlertTestResponse(BaseModel):
    results: dict[str, Optional[str]]  # channel -> error, or None if it went


class LogLineResponse(BaseModel):
    seq: int
    line: str
    level: str
    at: float


class LogsSinceResponse(BaseModel):
    lines: list[LogLineResponse]
    after: int  # pass back as `after` on the next poll


class LogShippingSettingsResponse(BaseModel):
    """Everything but the token and URL secret, which are only ever written."""

    enabled: bool
    min_level: str
    batch_seconds: float
    batch_lines: int


class LogShippingSettingsRequest(BaseModel):
    """Omitted fields are left as they are; an empty string clears url/token."""

    enabled: Optional[bool] = None
    url: Optional[str] = Field(default=None, max_length=500, pattern=_URL_OR_EMPTY)
    token: Optional[str] = Field(default=None, max_length=500, pattern=_ONE_LINE)
    min_level: Optional[Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]] = None
    batch_seconds: Optional[float] = Field(default=None, ge=0.5, le=60)
    batch_lines: Optional[int] = Field(default=None, ge=1, le=500)


class LogShippingResponse(BaseModel):
    settings: LogShippingSettingsResponse
    secrets_set: dict[str, bool]
    active: bool
    last_error: str
    last_sent_at: Optional[datetime]
    lines_sent: int


class LogShippingTestResponse(BaseModel):
    error: Optional[str]  # None if the test POST succeeded


class RestartResponse(BaseModel):
    available: bool  # installed with install-pi.sh, so the restart units exist
    requested: bool = False
