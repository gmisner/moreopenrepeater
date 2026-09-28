"""Pydantic request/response models for the repeater API."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

CourtesyToneStyle = Literal["beep", "high_low", "low_high", "triple", "chirp"]
WeatherSeverity = Literal["Minor", "Moderate", "Severe", "Extreme"]
CosSource = Literal["vox", "ctcss", "cm108"]


class StatusResponse(BaseModel):
    state: str
    ptt_active: bool
    cos_active: bool
    ctcss_hz: Optional[float]
    linked_nodes: list[str]
    last_clip: Optional[str]
    timestamp: float


class ConfigResponse(BaseModel):
    courtesy_tone_duration: float
    hang_time: float
    tot_duration: float
    id_interval: float
    id_audio_duration: float
    require_ctcss_hz: Optional[float]
    kerchunk_delay: float
    callsign: str
    id_mode: Literal["voice", "cw", "both"]
    cw_wpm: float
    cw_tone_hz: float
    courtesy_tone_asset_id: Optional[str]
    id_asset_id: Optional[str]
    timeout_tone_asset_id: Optional[str]
    courtesy_tone_style: CourtesyToneStyle
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
    vox_threshold_db: float
    vox_hold: float
    tx_gain_db: float


class ConfigUpdateRequest(BaseModel):
    courtesy_tone_duration: Optional[float] = Field(default=None, gt=0)
    hang_time: Optional[float] = Field(default=None, gt=0)
    tot_duration: Optional[float] = Field(default=None, gt=0)
    id_interval: Optional[float] = Field(default=None, gt=0)
    id_audio_duration: Optional[float] = Field(default=None, gt=0)
    require_ctcss_hz: Optional[float] = None
    clear_require_ctcss_hz: bool = False
    kerchunk_delay: Optional[float] = Field(default=None, ge=0, le=3)
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
    vox_threshold_db: Optional[float] = Field(default=None, ge=-90, le=0)
    vox_hold: Optional[float] = Field(default=None, ge=0, le=5)
    tx_gain_db: Optional[float] = Field(default=None, ge=-40, le=20)


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


class MacroCreateRequest(BaseModel):
    pattern: str = Field(min_length=1)
    description: str = ""
    command: str = Field(min_length=1)
    node_id: str = ""


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
        normalized = set()
        for value in times:
            hours, sep, minutes = value.strip().partition(":")
            if not (sep and hours.isdigit() and minutes.isdigit() and int(hours) < 24 and int(minutes) < 60):
                raise ValueError(f"{value!r} is not a HH:MM time")
            normalized.add(f"{int(hours):02d}:{int(minutes):02d}")
        return sorted(normalized)

    @field_validator("days")
    @classmethod
    def _valid_days(cls, days: list[int]) -> list[int]:
        if any(d < 0 or d > 6 for d in days):
            raise ValueError("days are 0 (Monday) to 6 (Sunday)")
        return sorted(set(days))

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


class AudioPreviewRequest(BaseModel):
    """Render `clip` as it would sound with `config` (unsaved dashboard
    edits) layered over the saved configuration."""

    clip: str = Field(min_length=1)
    config: dict[str, Any] = {}


class TTSInfoResponse(BaseModel):
    engine: Optional[str]


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


class ActivitySummaryResponse(BaseModel):
    since: datetime
    until: datetime
    rx_seconds: float
    rx_count: int
    kerchunks: int
    kerchunks_filtered: int
    timeouts: int
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
    hardware_ptt: bool


class LoginRequest(BaseModel):
    username: str
    password: str


class SessionResponse(BaseModel):
    auth_required: bool
    authenticated: bool
    username: Optional[str]


class AssetResponse(BaseModel):
    id: str
    kind: Literal["courtesy_tone", "id", "timeout_tone", "custom"]
    filename: str
    uploaded_at: str
