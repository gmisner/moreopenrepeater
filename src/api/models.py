"""Pydantic request/response models for the repeater API."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from controller.macros import ACTIONS_NEEDING_ARGUMENT, MacroAction

from .gpio import parse_gpio_command

CourtesyToneStyle = Literal["beep", "high_low", "low_high", "triple", "chirp"]
NetLinks = Literal["leave", "disconnect", "connect"]
CourtesyToneVariant = Literal["same", "beep", "high_low", "low_high", "triple", "chirp"]  # "same": the local one
WeatherSeverity = Literal["Minor", "Moderate", "Severe", "Extreme"]
CosSource = Literal["vox", "ctcss", "cm108", "gpio"]
Polarity = Literal["low", "high"]
PttOutput = Literal["cm108", "gpio"]
# BCM numbers on the 40-pin header; GPIO0/1 are reserved for HAT EEPROMs.
PiGpioPin = Annotated[int, Field(ge=2, le=27)]
Role = Literal["admin", "operator", "viewer"]

# A hostname, or an IPv4/IPv6 address with an optional /prefix, as PJSIP's identify `match` takes.
_SOURCE = re.compile(r"^(?:[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?|[0-9A-Fa-f:.]{2,45})(?:/\d{1,3})?$")


class StatusResponse(BaseModel):
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


# GPIO3 is the PTT pin.
GpioPinNumber = Literal["1", "2", "4", "5", "6", "7", "8"]


class GpioPinConfig(BaseModel):
    mode: Literal["output", "input"]
    name: str = Field(default="", max_length=30)
    # Inputs only: on when the pin reads low (a switch to ground), and what to
    # do when the input turns on or off.
    invert: bool = False
    on_say: str = Field(default="", max_length=200)
    off_say: str = Field(default="", max_length=200)
    on_macro: str = Field(default="", pattern=r"^[0-9A-D*#]{0,16}$")
    off_macro: str = Field(default="", pattern=r"^[0-9A-D*#]{0,16}$")


NodeNumber = Annotated[str, Field(pattern=r"^[0-9]{1,10}$")]


class LinkFavorite(BaseModel):
    node: NodeNumber
    name: str = Field(default="", max_length=40)
    monitor: bool = False


def hh_mm(value: str) -> str:
    hours, sep, minutes = value.strip().partition(":")
    if not (sep and hours.isdigit() and minutes.isdigit() and int(hours) < 24 and int(minutes) < 60):
        raise ValueError(f"{value!r} is not a HH:MM time")
    return f"{int(hours):02d}:{int(minutes):02d}"


def weekdays(days: list[int]) -> list[int]:
    if any(d < 0 or d > 6 for d in days):
        raise ValueError("days are 0 (Monday) to 6 (Sunday)")
    return sorted(set(days))


class LinkSchedule(BaseModel):
    node: NodeNumber
    name: str = Field(default="", max_length=40)
    days: list[int] = Field(min_length=1)
    time: str
    minutes: int = Field(default=60, ge=0, le=12 * 60)  # 0 = stay linked
    monitor: bool = False
    enabled: bool = True

    @field_validator("time")
    @classmethod
    def _valid_time(cls, value: str) -> str:
        return hh_mm(value)

    @field_validator("days")
    @classmethod
    def _valid_days(cls, days: list[int]) -> list[int]:
        return weekdays(days)


class NetSchedule(BaseModel):
    days: list[int] = Field(min_length=1)
    time: str
    minutes: int = Field(default=60, ge=0, le=12 * 60)  # 0 = until ended (or net_max_minutes)
    enabled: bool = True

    @field_validator("time")
    @classmethod
    def _valid_time(cls, value: str) -> str:
        return hh_mm(value)

    @field_validator("days")
    @classmethod
    def _valid_days(cls, days: list[int]) -> list[int]:
        return weekdays(days)


class GpioSchedule(BaseModel):
    pin: Literal[1, 2, 4, 5, 6, 7, 8]
    days: list[int] = Field(min_length=1)
    time: str
    minutes: int = Field(default=60, ge=0, le=24 * 60)  # 0 = leave it on
    enabled: bool = True

    @field_validator("time")
    @classmethod
    def _valid_time(cls, value: str) -> str:
        return hh_mm(value)

    @field_validator("days")
    @classmethod
    def _valid_days(cls, days: list[int]) -> list[int]:
        return weekdays(days)


class ConfigResponse(BaseModel):
    courtesy_tone_duration: float
    hang_time: float
    tot_duration: float
    id_interval: float
    idle_id: bool
    id_audio_duration: float
    require_ctcss_hz: Optional[float]
    kerchunk_delay: float
    lockout_timeouts: int
    lockout_window: float
    lockout_clear_after: float
    transmitter_enabled: bool
    callsign: str
    id_mode: Literal["voice", "cw", "both"]
    cw_wpm: float
    cw_tone_hz: float
    courtesy_tone_asset_id: Optional[str]
    id_asset_id: Optional[str]
    timeout_tone_asset_id: Optional[str]
    courtesy_tone_style: CourtesyToneStyle
    courtesy_tone_link_style: CourtesyToneVariant = "same"
    courtesy_tone_link_asset_id: Optional[str] = None
    courtesy_tone_patch_style: CourtesyToneVariant = "same"
    courtesy_tone_patch_asset_id: Optional[str] = None
    voice_id_text: str
    id_phonetic: bool
    tts_voice: str
    aprs_enabled: bool
    aprs_server: str
    aprs_port: int
    aprs_callsign: str
    aprs_lat: Optional[float]
    aprs_lon: Optional[float]
    aprs_comment: str
    aprs_beacon_interval: float
    aprs_symbol: str
    aprs_frequency_mhz: Optional[float]
    aprs_offset_mhz: Optional[float]
    aprs_tone_hz: Optional[float]
    aprs_map_enabled: bool
    aprs_map_radius_km: float
    aprs_map_hours: float
    aprs_map_tiles: str
    distance_units: Literal["mi", "km"]
    wx_alerts_enabled: bool
    wx_lat: Optional[float]
    wx_lon: Optional[float]
    wx_min_severity: WeatherSeverity
    wx_poll_interval: float
    wx_repeat_minutes: float
    audio_enabled: bool
    audio_input_device: str
    audio_output_device: str
    cos_source: CosSource
    cos_polarity: Polarity
    cos_gpio_pin: int
    ptt_output: PttOutput
    ptt_gpio_pin: int
    ptt_polarity: Polarity
    vox_threshold_db: float
    vox_hold: float
    tx_gain_db: float
    tx_ctcss_hz: Optional[float]
    tx_ctcss_level_db: float
    record_transmissions: bool
    recording_retention_days: float
    autopatch_enabled: bool
    autopatch_access_code: str
    autopatch_hangup_code: str
    autopatch_dial_string: str
    autopatch_ten_digit_prefix: str
    autopatch_caller_id: str
    autopatch_allowed: str
    autopatch_blocked: str
    autopatch_max_call_seconds: float
    autopatch_ring_seconds: float
    autopatch_incoming_enabled: bool
    autopatch_incoming_pin: str
    backup_enabled: bool
    backup_interval_hours: float
    backup_keep: int
    backup_include_recordings: bool
    gpio_pins: dict[GpioPinNumber, GpioPinConfig] = {}
    gpio_schedules: list[GpioSchedule] = []
    link_favorites: list[LinkFavorite] = []
    link_schedules: list[LinkSchedule] = []
    gmrs_mode: bool = False
    net_name: str = "Net"
    net_tot_duration: float = 600.0
    net_hang_time: float = 1.0
    net_courtesy_tone_style: CourtesyToneVariant = "same"
    net_courtesy_tone_asset_id: Optional[str] = None
    net_hold_autopatch: bool = True
    net_hold_announcements: bool = True
    net_links: NetLinks = "leave"
    net_link_node: str = ""
    net_start_say: str = ""
    net_end_say: str = ""
    net_max_minutes: float = 120.0
    net_schedules: list[NetSchedule] = []


class ConfigUpdateRequest(BaseModel):
    courtesy_tone_duration: Optional[float] = Field(default=None, gt=0)
    hang_time: Optional[float] = Field(default=None, gt=0)
    tot_duration: Optional[float] = Field(default=None, gt=0)
    id_interval: Optional[float] = Field(default=None, gt=0)
    idle_id: Optional[bool] = None
    id_audio_duration: Optional[float] = Field(default=None, gt=0)
    require_ctcss_hz: Optional[float] = None
    clear_require_ctcss_hz: bool = False
    kerchunk_delay: Optional[float] = Field(default=None, ge=0, le=3)
    lockout_timeouts: Optional[int] = Field(default=None, ge=0, le=20)
    lockout_window: Optional[float] = Field(default=None, ge=60, le=86400)
    lockout_clear_after: Optional[float] = Field(default=None, ge=5, le=3600)
    transmitter_enabled: Optional[bool] = None
    callsign: Optional[str] = None
    id_mode: Optional[Literal["voice", "cw", "both"]] = None
    cw_wpm: Optional[float] = Field(default=None, gt=0)
    cw_tone_hz: Optional[float] = Field(default=None, gt=0)
    courtesy_tone_asset_id: Optional[str] = None
    id_asset_id: Optional[str] = None
    timeout_tone_asset_id: Optional[str] = None
    clear_courtesy_tone_asset_id: bool = False
    clear_id_asset_id: bool = False
    clear_timeout_tone_asset_id: bool = False
    courtesy_tone_style: Optional[CourtesyToneStyle] = None
    courtesy_tone_link_style: Optional[CourtesyToneVariant] = None
    courtesy_tone_link_asset_id: Optional[str] = None
    clear_courtesy_tone_link_asset_id: bool = False
    courtesy_tone_patch_style: Optional[CourtesyToneVariant] = None
    courtesy_tone_patch_asset_id: Optional[str] = None
    clear_courtesy_tone_patch_asset_id: bool = False
    voice_id_text: Optional[str] = None
    id_phonetic: Optional[bool] = None
    tts_voice: Optional[str] = None
    aprs_enabled: Optional[bool] = None
    aprs_server: Optional[str] = None
    aprs_port: Optional[int] = Field(default=None, gt=0)
    aprs_callsign: Optional[str] = None
    aprs_lat: Optional[float] = None
    aprs_lon: Optional[float] = None
    aprs_comment: Optional[str] = None
    aprs_beacon_interval: Optional[float] = Field(default=None, gt=0)
    # Table ("/", "\\" or an overlay letter/digit) then any printable symbol code.
    aprs_symbol: Optional[str] = Field(default=None, pattern=r"^[/\\A-Z0-9][!-~]$")
    aprs_frequency_mhz: Optional[float] = Field(default=None, ge=28, le=1300)
    # The freq spec encodes offsets in 10 kHz steps with three digits.
    aprs_offset_mhz: Optional[float] = Field(default=None, ge=-9.99, le=9.99)
    aprs_tone_hz: Optional[float] = Field(default=None, ge=60, le=260)
    clear_aprs_frequency_mhz: bool = False
    clear_aprs_offset_mhz: bool = False
    clear_aprs_tone_hz: bool = False
    aprs_map_enabled: Optional[bool] = None
    aprs_map_radius_km: Optional[float] = Field(default=None, ge=1, le=500)
    aprs_map_hours: Optional[float] = Field(default=None, ge=0.25, le=48)
    aprs_map_tiles: Optional[str] = Field(default=None, max_length=300, pattern=r"^$|^https?://")
    distance_units: Optional[Literal["mi", "km"]] = None
    clear_aprs_lat: bool = False
    clear_aprs_lon: bool = False
    wx_alerts_enabled: Optional[bool] = None
    wx_lat: Optional[float] = Field(default=None, ge=-90, le=90)
    wx_lon: Optional[float] = Field(default=None, ge=-180, le=180)
    clear_wx_lat: bool = False
    clear_wx_lon: bool = False
    wx_min_severity: Optional[WeatherSeverity] = None
    wx_poll_interval: Optional[float] = Field(default=None, ge=30)
    wx_repeat_minutes: Optional[float] = Field(default=None, ge=0)
    audio_enabled: Optional[bool] = None
    audio_input_device: Optional[str] = None
    audio_output_device: Optional[str] = None
    cos_source: Optional[CosSource] = None
    cos_polarity: Optional[Polarity] = None
    cos_gpio_pin: Optional[PiGpioPin] = None
    ptt_output: Optional[PttOutput] = None
    ptt_gpio_pin: Optional[PiGpioPin] = None
    ptt_polarity: Optional[Polarity] = None
    vox_threshold_db: Optional[float] = Field(default=None, ge=-90, le=0)
    vox_hold: Optional[float] = Field(default=None, ge=0, le=5)
    tx_gain_db: Optional[float] = Field(default=None, ge=-40, le=20)
    tx_ctcss_hz: Optional[float] = Field(default=None, ge=60, le=260)
    clear_tx_ctcss_hz: bool = False
    tx_ctcss_level_db: Optional[float] = Field(default=None, ge=-40, le=-6)
    record_transmissions: Optional[bool] = None
    recording_retention_days: Optional[float] = Field(default=None, ge=0.1, le=365)
    autopatch_enabled: Optional[bool] = None
    # "#" ends the phone number, so it can't be part of the access code.
    autopatch_access_code: Optional[str] = Field(default=None, pattern=r"^[0-9A-D*]{1,8}$")
    autopatch_hangup_code: Optional[str] = Field(default=None, pattern=r"^[0-9A-D*#]{1,8}$")
    # These go into AMI header lines, so no line breaks.
    autopatch_dial_string: Optional[str] = Field(default=None, max_length=200, pattern=r"^[^\r\n]*\{number\}[^\r\n]*$")
    autopatch_ten_digit_prefix: Optional[Literal["", "1", "+1"]] = None
    autopatch_caller_id: Optional[str] = Field(default=None, max_length=80, pattern=r"^[^\r\n]*$")
    autopatch_allowed: Optional[str] = Field(default=None, max_length=500, pattern=r"^[0-9XNZxnz,\s]*$")
    autopatch_blocked: Optional[str] = Field(default=None, max_length=500, pattern=r"^[0-9XNZxnz,\s]*$")
    autopatch_max_call_seconds: Optional[float] = Field(default=None, ge=30, le=3600)
    autopatch_ring_seconds: Optional[float] = Field(default=None, ge=5, le=120)
    autopatch_incoming_enabled: Optional[bool] = None
    autopatch_incoming_pin: Optional[str] = Field(default=None, pattern=r"^(?:[0-9]{4,8})?$")
    backup_enabled: Optional[bool] = None
    backup_interval_hours: Optional[float] = Field(default=None, ge=1, le=720)
    backup_keep: Optional[int] = Field(default=None, ge=1, le=100)
    backup_include_recordings: Optional[bool] = None
    gpio_pins: Optional[dict[GpioPinNumber, GpioPinConfig]] = None
    gpio_schedules: Optional[list[GpioSchedule]] = Field(default=None, max_length=50)
    link_favorites: Optional[list[LinkFavorite]] = Field(default=None, max_length=50)
    link_schedules: Optional[list[LinkSchedule]] = Field(default=None, max_length=50)
    gmrs_mode: Optional[bool] = None
    net_name: Optional[str] = Field(default=None, min_length=1, max_length=60)
    net_tot_duration: Optional[float] = Field(default=None, ge=30, le=3600)
    net_hang_time: Optional[float] = Field(default=None, gt=0, le=30)
    net_courtesy_tone_style: Optional[CourtesyToneVariant] = None
    net_courtesy_tone_asset_id: Optional[str] = None
    clear_net_courtesy_tone_asset_id: bool = False
    net_hold_autopatch: Optional[bool] = None
    net_hold_announcements: Optional[bool] = None
    net_links: Optional[NetLinks] = None
    net_link_node: Optional[str] = Field(default=None, pattern=r"^[0-9]{0,10}$")
    net_start_say: Optional[str] = Field(default=None, max_length=500)
    net_end_say: Optional[str] = Field(default=None, max_length=500)
    net_max_minutes: Optional[float] = Field(default=None, ge=10, le=24 * 60)
    net_schedules: Optional[list[NetSchedule]] = Field(default=None, max_length=20)

    @model_validator(mode="after")
    def _net_link_needs_a_node(self) -> "ConfigUpdateRequest":
        if self.net_links == "connect" and self.net_link_node == "":
            raise ValueError("choose the node to link for nets")
        return self


class AutopatchDialRequest(BaseModel):
    number: str = Field(min_length=1, max_length=15, pattern=r"^[0-9]+$")


class AutopatchCallResponse(BaseModel):
    number: str
    actor: str
    state: Literal["dialing", "connected", "ended"]
    started_at: float
    connected_at: Optional[float]
    ended_at: Optional[float]
    result: str
    direction: Literal["outgoing", "incoming"]


class AutopatchStatusResponse(BaseModel):
    available: bool  # Asterisk is configured and the AudioSocket server is listening
    configured: bool
    error: Optional[str]
    enabled: bool
    call: Optional[AutopatchCallResponse]
    last_call: Optional[AutopatchCallResponse]


class SipTrunkRequest(BaseModel):
    # These are written into pjsip.conf and SIP URIs.
    server: str = Field(max_length=253, pattern=r"^[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?$")
    port: int = Field(default=5060, ge=1, le=65535)
    transport: Literal["udp", "tcp"] = "udp"
    username: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._~+-]+$")
    auth_username: str = Field(default="", max_length=64, pattern=r"^[A-Za-z0-9._~+-]*$")
    # Blank keeps the saved one. Asterisk trims surrounding spaces from values.
    password: str = Field(default="", max_length=128, pattern=r"^(?:[!-~](?:[ -~]*[!-~])?)?$")
    registers: bool = True
    ten_digit_prefix: Literal["", "1", "+1"] = ""
    # Hostnames, IP addresses or networks (a.b.c.d/nn) calls in come from, besides the server.
    incoming_from: list[str] = Field(default=[], max_length=32)

    @field_validator("incoming_from")
    @classmethod
    def _sources(cls, sources: list[str]) -> list[str]:
        for source in sources:
            if not _SOURCE.match(source):
                raise ValueError(f"{source!r} isn't a hostname, IP address or network")
        return sources


class SipTrunkSettings(BaseModel):
    server: str
    port: int
    transport: Literal["udp", "tcp"]
    username: str
    auth_username: str
    has_password: bool
    registers: bool
    incoming_from: list[str]
    answers_calls: bool  # Asterisk has the dialplan for calls in (lines saved by older versions don't)


class SipTrunkStatusResponse(BaseModel):
    configured: bool  # the AMI settings are there
    error: Optional[str]
    missing_modules: list[str]
    trunk: Optional[SipTrunkSettings]
    registration: Optional[str]  # Registered, Unregistered, Rejected, ...; None when not registering
    reachability: Optional[str]  # Reachable, Unreachable, ... from Asterisk's OPTIONS pings
    in_use: bool  # the autopatch dial string points at this trunk
    ten_digit_prefix: str


class SimulateCOSRequest(BaseModel):
    active: bool


class SimulateCTCSSRequest(BaseModel):
    tone_hz: Optional[float] = None


class SimulateDTMFRequest(BaseModel):
    digit: str = Field(min_length=1, max_length=1)


class SimulateRemoteKeyedRequest(BaseModel):
    node_id: str
    keyed: bool


class MacroResponse(BaseModel):
    pattern: str
    description: str
    command: str
    node_id: str
    action: MacroAction
    needs_code: bool


class MacroCreateRequest(BaseModel):
    pattern: str = Field(min_length=1, pattern=r"^[0-9A-D*#]+$")
    description: str = ""
    command: str = ""
    node_id: str = ""
    action: MacroAction = "link"
    needs_code: bool = False

    @model_validator(mode="after")
    def _command_when_needed(self) -> "MacroCreateRequest":
        if self.action in ACTIONS_NEEDING_ARGUMENT and not self.command.strip():
            raise ValueError(f"a {self.action!r} macro needs a command")
        if self.action == "gpio":
            parse_gpio_command(self.command)
        return self


MAX_ANNOUNCEMENT_LENGTH = 2000


class AnnouncementFields(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    message: str = Field(default="", max_length=MAX_ANNOUNCEMENT_LENGTH)
    asset_id: Optional[str] = None
    kind: Literal["interval", "weekly"] = "interval"
    every_minutes: int = Field(default=60, ge=1, le=7 * 24 * 60)
    times: list[str] = []
    days: list[int] = [0, 1, 2, 3, 4, 5, 6]
    enabled: bool = True

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


class AudioPreviewRequest(BaseModel):
    """Render `clip` as it would sound with `config` (unsaved dashboard
    edits) layered over the saved configuration."""

    clip: str = Field(min_length=1)
    config: dict[str, Any] = {}
    net: bool = False  # as it would sound during a net


class TTSInfoResponse(BaseModel):
    engine: Optional[str]


UpdateChannel = Literal["stable", "beta", "dev"]


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


_URL_OR_EMPTY = r"^$|^https?://[^\s]+$"
_ONE_LINE = r"^[^\r\n]*$"  # these go into email headers


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


class WeatherAlertResponse(BaseModel):
    id: str
    event: str
    severity: str
    urgency: str
    headline: str
    area: str
    expires: Optional[str]
    ends: Optional[str]
    announced: bool
    speech: str


class WeatherStatusResponse(BaseModel):
    enabled: bool
    last_checked: Optional[datetime]
    last_error: Optional[str]
    alerts: list[WeatherAlertResponse]


class ActivityDay(BaseModel):
    date: str
    rx_seconds: float
    tx_seconds: float
    rx_count: int


class RecordingResponse(BaseModel):
    id: str
    started_at: datetime
    duration: float


class ActivitySummaryResponse(BaseModel):
    since: datetime
    until: datetime
    rx_seconds: float
    rx_count: int
    kerchunks: int
    kerchunks_filtered: int
    timeouts: int
    lockouts: int = 0
    longest_rx_seconds: float
    tx_seconds: float
    ids: int
    announcements: int
    by_hour: list[float]
    by_day: list[ActivityDay]


class TransmissionResponse(BaseModel):
    started_at: datetime
    duration: float
    timed_out: bool


class AudioDeviceResponse(BaseModel):
    name: str
    inputs: int
    outputs: int
    default_samplerate: float


class LinkAudioResponse(BaseModel):
    configured: bool
    running: bool
    error: Optional[str]
    node: Optional[str]
    keyed: bool


class AllStarNodeInfo(BaseModel):
    number: str
    rxchannel: str
    duplex: str
    controlled: bool


class AllStarStatusResponse(BaseModel):
    available: bool
    manual: bool
    can_restart: bool
    restart_needed: bool
    error: Optional[str]
    nodes: list[AllStarNodeInfo]
    node: Optional[str]
    audio: LinkAudioResponse


class GpioPinStatus(BaseModel):
    pin: int
    name: str
    mode: Optional[Literal["output", "input"]]
    on: Optional[bool]
    until: Optional[datetime] = None  # a scheduled output's switch-off, local time


class GpioScheduleStatus(BaseModel):
    pin: int
    next_start: Optional[datetime]
    until: Optional[datetime]


class GpioStatusResponse(BaseModel):
    available: bool
    error: Optional[str]
    pins: list[GpioPinStatus]
    schedules: list[GpioScheduleStatus] = []


class GpioOutputRequest(BaseModel):
    on: bool


class LinkInfo(BaseModel):
    node: str
    kind: Literal["allstar", "echolink"]
    callsign: str
    description: str
    location: str
    mode: Optional[str] = None
    keyed: bool = False
    name: str = ""
    monitor: bool = False
    until: Optional[datetime] = None  # a scheduled link's end, local time


class LinkScheduleStatus(BaseModel):
    node: str
    next_start: Optional[datetime]
    until: Optional[datetime]


class LinksResponse(BaseModel):
    available: bool
    node: Optional[str]
    error: Optional[str]
    links: list[LinkInfo]
    favorites: list[LinkInfo]
    schedules: list[LinkScheduleStatus] = []


class LinkConnectRequest(BaseModel):
    node: NodeNumber
    monitor: bool = False


class AllStarNodeRequest(BaseModel):
    node: str = Field(pattern=r"^[0-9]{1,10}$")


# `;` starts a comment in Asterisk's config files, so it can't be in a value.
_CONF_TEXT = r"^[^;\\\r\n]*$"


class EchoLinkRequest(BaseModel):
    callsign: str = Field(pattern=r"^[A-Z0-9]{3,8}(-[LR])?$")
    password: str = Field(default="", max_length=64, pattern=r"^[^\s;\\]*$")
    name: str = Field(min_length=1, max_length=40, pattern=_CONF_TEXT)
    location: str = Field(min_length=1, max_length=40, pattern=_CONF_TEXT)
    email: str = Field(max_length=80, pattern=r"^[^\s;@\\]+@[^\s;@\\]+$")
    node_number: str = Field(pattern=r"^[0-9]{1,7}$")
    lat: float = Field(default=0.0, ge=-90, le=90)
    lon: float = Field(default=0.0, ge=-180, le=180)
    frequency_mhz: float = Field(default=0.0, ge=0, le=3000)
    tone_hz: float = Field(default=0.0, ge=0, le=300)
    max_stations: int = Field(default=20, ge=1, le=100)


class EchoLinkStationInfo(BaseModel):
    callsign: str
    name: str
    location: str
    email: str
    node_number: str
    has_password: bool
    lat: float
    lon: float
    frequency_mhz: float
    tone_hz: float
    max_stations: int
    astnode: str
    set_here: bool


class EchoLinkStatusResponse(BaseModel):
    available: bool
    enabled: bool
    loaded: Optional[bool]
    error: Optional[str]
    settings: Optional[EchoLinkStationInfo]


class AudioEngineResponse(BaseModel):
    enabled: bool
    running: bool
    error: Optional[str]
    input_device: str
    output_device: str
    sample_rate: int
    device_sample_rate: Optional[int]
    rx_level_db: float
    cos_open: bool
    ctcss_hz: Optional[float]
    transmitting: bool
    dropped_input_blocks: int
    starved_output_blocks: int
    hardware_ptt: Optional[str]  # "cm108" or "gpio"
    listeners: int = 0


class LoginRequest(BaseModel):
    username: str
    password: str


class SessionResponse(BaseModel):
    auth_required: bool
    authenticated: bool
    username: Optional[str]
    role: Optional[Role] = None


class UserResponse(BaseModel):
    username: str
    role: Role
    builtin: bool  # the env-configured admin; can't be edited here


class UserCreateRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._@-]+$")
    password: str = Field(min_length=8, max_length=200)
    role: Role


class UserUpdateRequest(BaseModel):
    password: Optional[str] = Field(default=None, min_length=8, max_length=200)
    role: Optional[Role] = None


class AprsStationResponse(BaseModel):
    name: str
    source: str
    kind: str
    category: str
    lat: float
    lon: float
    symbol_table: str
    symbol_code: str
    comment: str
    course: Optional[int]
    speed_kmh: Optional[float]
    altitude_m: Optional[float]
    weather: Optional[dict]
    first_heard: float  # unix time
    last_heard: float
    packets: int
    trail: list[list[float]]  # [[lat, lon], ...] oldest first
    distance_km: Optional[float] = None  # from the repeater
    bearing: Optional[int] = None


class AprsMapResponse(BaseModel):
    enabled: bool
    center: Optional[list[float]]  # [lat, lon] of the repeater
    radius_km: float
    hours: float
    tiles: str
    distance_units: Literal["mi", "km"]
    connected: bool
    server: str
    error: Optional[str]
    last_packet: Optional[datetime]
    packets: int
    stations: list[AprsStationResponse]


class AuditEntryResponse(BaseModel):
    at: datetime
    actor: str
    action: str
    detail: str
    status: int


class AssetResponse(BaseModel):
    id: str
    kind: Literal["courtesy_tone", "id", "timeout_tone", "custom"]
    filename: str
    uploaded_at: str


class NetCheckIn(BaseModel):
    id: str
    callsign: str
    at: float  # unix time
    notes: str


class NetSummary(BaseModel):
    id: str
    name: str
    started_at: float
    ends_at: float  # when it ends by itself
    started_by: str
    scheduled_for: Optional[str]
    linked_node: Optional[str]  # linked for the net; dropped at the end
    link_error: Optional[str]
    ended_at: Optional[float] = None
    ended_by: Optional[str] = None


class RunningNet(NetSummary):
    checkins: list[NetCheckIn]


class PastNet(NetSummary):
    checkin_count: int


class NetStatusResponse(BaseModel):
    current: Optional[RunningNet]
    past: list[PastNet]
    next_scheduled: Optional[datetime]


class NetStartRequest(BaseModel):
    name: str = Field(default="", max_length=60)


class NetCheckInRequest(BaseModel):
    callsign: str = Field(pattern=r"^[A-Za-z0-9/-]{1,15}$")
    notes: str = Field(default="", max_length=300)


class ControlCodeStatus(BaseModel):
    enrolled: bool
    since: Optional[float] = None
    pending: bool = False


class ControlCodeSetup(BaseModel):
    """Shown once: the secret for an authenticator app, as text and a QR code."""

    secret: str
    uri: str
    qr_svg: str


class ControlCodeConfirmRequest(BaseModel):
    code: str = Field(pattern=r"^[0-9]{6}$")


class ControlCodeUser(BaseModel):
    username: str
    since: float
