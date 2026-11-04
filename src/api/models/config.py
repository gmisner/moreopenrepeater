"""Config and related embedded request/response models."""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from controller.config import CourtesyStyle as CourtesyToneStyle
from controller.config import CourtesyVariant as CourtesyToneVariant
from controller.remote_base import CTCSS_TONES, parse_ranges
from playout.tones import parse_custom_tone

from ..config_schema import ConfigResponse as _GeneratedConfigResponse
from .types import (
    CosSource,
    FanOutput,
    GpioPinNumber,
    LinkRadioPtt,
    MonitorSquelch,
    NetLinks,
    NodeNumber,
    PiGpioPin,
    Polarity,
    PttOutput,
    PublicPageMode,
    SerialDevice,
    SerialInputLine,
    SerialOutputLine,
    TranscriptionEngine,
    WeatherSeverity,
    hh_mm,
    weekdays,
)

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
    callsign: str = ""


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

class MetarAirport(BaseModel):
    icao: str = Field(pattern=r"^[A-Z0-9]{4}$")
    name: str = Field(default="", max_length=40)  # how it's spoken; blank spells the code

    @field_validator("icao", mode="before")
    @classmethod
    def _upper(cls, value: Any) -> Any:
        return value.strip().upper() if isinstance(value, str) else value


class SiteSensor(BaseModel):
    """Named analog site sensor (DS18B20 or ADS1115 channel)."""

    id: str = Field(min_length=1, max_length=32, pattern=r"^[a-z][a-z0-9_-]*$")
    name: str = Field(min_length=1, max_length=40)
    type: Literal["ds18b20", "ads1115"]
    units: str = Field(default="", max_length=16)
    scale: float = Field(default=1.0)
    offset: float = Field(default=0.0)
    # DS18B20: w1 device id (28-...); blank uses the first sensor found.
    device: str = Field(default="", max_length=32, pattern=r"^$|^28-[0-9a-fA-F]+$")
    # ADS1115:
    bus: int = Field(default=1, ge=0, le=20)
    address: int = Field(default=0x48, ge=0x48, le=0x4B)
    channel: int = Field(default=0, ge=0, le=3)
    high: Optional[float] = None
    low: Optional[float] = None
    hysteresis: float = Field(default=0.5, ge=0, le=1000)
    alert: bool = True
    speak_on_alert: bool = False

    @field_validator("id", mode="before")
    @classmethod
    def _lower_id(cls, value: Any) -> Any:
        return value.strip().lower() if isinstance(value, str) else value

    @model_validator(mode="after")
    def _thresholds(self) -> "SiteSensor":
        if self.high is not None and self.low is not None and self.high <= self.low:
            raise ValueError("high threshold must be greater than low")
        return self


class LinkFavorite(BaseModel):
    node: NodeNumber
    name: str = Field(default="", max_length=40)
    monitor: bool = False


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


# Generated from controller.config.RepeaterConfig facets — do not hand-edit field lists.
ConfigResponse = _GeneratedConfigResponse


class ConfigUpdateRequest(BaseModel):
    # --- facet: identity_timing ---
    courtesy_tone_duration: Optional[float] = Field(default=None, gt=0)
    hang_time: Optional[float] = Field(default=None, gt=0)
    tot_duration: Optional[float] = Field(default=None, gt=0)
    id_interval: Optional[float] = Field(default=None, gt=0)
    idle_id: Optional[bool] = None
    id_audio_duration: Optional[float] = Field(default=None, gt=0)
    node_mode: Optional[Literal["repeater", "simplex"]] = None
    simplex_courtesy_tone: Optional[bool] = None
    id_skip_short_seconds: Optional[float] = Field(default=None, ge=0, le=10)
    long_id_mode: Optional[Literal["off", "voice", "both"]] = None
    long_id_interval: Optional[float] = Field(default=None, ge=300, le=86400)
    long_id_text: Optional[str] = Field(default=None, max_length=300)
    long_id_asset_id: Optional[str] = None
    clear_long_id_asset_id: bool = False
    require_ctcss_hz: Optional[float] = None
    clear_require_ctcss_hz: bool = False
    access_mode: Optional[Literal["carrier", "ctcss_open", "tone_burst"]] = None
    tone_burst_ms: Optional[float] = Field(default=None, ge=100, le=2000)
    # --- facet: radio_audio ---
    rx_deemphasis: Optional[bool] = None
    tx_preemphasis: Optional[bool] = None
    kerchunk_delay: Optional[float] = Field(default=None, ge=0, le=3)
    lockout_timeouts: Optional[int] = Field(default=None, ge=0, le=20)
    lockout_window: Optional[float] = Field(default=None, ge=60, le=86400)
    lockout_clear_after: Optional[float] = Field(default=None, ge=5, le=3600)
    transmitter_enabled: Optional[bool] = None
    callsign: Optional[str] = None
    id_mode: Optional[Literal["voice", "cw", "both"]] = None
    cw_wpm: Optional[float] = Field(default=None, gt=0)
    cw_tone_hz: Optional[float] = Field(default=None, gt=0)
    cw_id_suffix: Optional[str] = Field(default=None, pattern=r"^(/[A-Za-z0-9]{1,5})?$")
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
    courtesy_tone_custom: Optional[str] = Field(default=None, max_length=60)
    voice_id_text: Optional[str] = None
    id_phonetic: Optional[bool] = None
    tts_voice: Optional[str] = None
    # --- facet: aprs ---
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
    # --- facet: weather ---
    wx_alerts_enabled: Optional[bool] = None
    wx_lat: Optional[float] = Field(default=None, ge=-90, le=90)
    wx_lon: Optional[float] = Field(default=None, ge=-180, le=180)
    clear_wx_lat: bool = False
    clear_wx_lon: bool = False
    wx_min_severity: Optional[WeatherSeverity] = None
    wx_poll_interval: Optional[float] = Field(default=None, ge=30)
    wx_repeat_minutes: Optional[float] = Field(default=None, ge=0)
    metar_airports: Optional[list[MetarAirport]] = Field(default=None, max_length=20)
    audio_enabled: Optional[bool] = None
    setup_wizard_done: Optional[bool] = None
    audio_input_device: Optional[str] = None
    audio_output_device: Optional[str] = None
    radio_model: Optional[str] = Field(default=None, max_length=80)
    radio_frequency: Optional[str] = Field(default=None, max_length=80)
    radio_notes: Optional[str] = Field(default=None, max_length=500)
    cos_source: Optional[CosSource] = None
    cos_polarity: Optional[Polarity] = None
    cos_gpio_pin: Optional[PiGpioPin] = None
    cos_serial_device: Optional[SerialDevice] = None
    cos_serial_line: Optional[SerialInputLine] = None
    ptt_output: Optional[PttOutput] = None
    ptt_gpio_pin: Optional[PiGpioPin] = None
    ptt_serial_device: Optional[SerialDevice] = None
    ptt_serial_line: Optional[SerialOutputLine] = None
    ptt_polarity: Optional[Polarity] = None
    vox_threshold_db: Optional[float] = Field(default=None, ge=-90, le=0)
    squelch_tail_ms: Optional[float] = Field(default=None, ge=0, le=300)
    tx_delay_ms: Optional[float] = Field(default=None, ge=0, le=500)
    dtmf_mute: Optional[bool] = None
    # --- facet: monitor ---
    monitor_enabled: Optional[bool] = None
    monitor_name: Optional[str] = Field(default=None, max_length=40)
    monitor_input_device: Optional[str] = Field(default=None, max_length=200)
    monitor_squelch: Optional[MonitorSquelch] = None
    monitor_vox_threshold_db: Optional[float] = Field(default=None, ge=-90, le=0)
    monitor_gpio_pin: Optional[PiGpioPin] = None
    monitor_gpio_polarity: Optional[Polarity] = None
    monitor_gain_db: Optional[float] = Field(default=None, ge=-20, le=20)
    monitor_record: Optional[bool] = None
    # --- facet: link_radio ---
    link_radio_enabled: Optional[bool] = None
    link_radio_name: Optional[str] = Field(default=None, max_length=40)
    link_radio_model: Optional[str] = Field(default=None, max_length=80)
    link_radio_frequency: Optional[str] = Field(default=None, max_length=80)
    link_radio_notes: Optional[str] = Field(default=None, max_length=500)
    link_radio_input_device: Optional[str] = Field(default=None, max_length=200)
    link_radio_output_device: Optional[str] = Field(default=None, max_length=200)
    link_radio_cos: Optional[CosSource] = None
    link_radio_cos_polarity: Optional[Polarity] = None
    link_radio_cos_gpio_pin: Optional[PiGpioPin] = None
    link_radio_cos_serial_device: Optional[SerialDevice] = None
    link_radio_cos_serial_line: Optional[SerialInputLine] = None
    link_radio_vox_threshold_db: Optional[float] = Field(default=None, ge=-90, le=0)
    link_radio_ptt: Optional[LinkRadioPtt] = None
    link_radio_ptt_gpio_pin: Optional[PiGpioPin] = None
    link_radio_ptt_serial_device: Optional[SerialDevice] = None
    link_radio_ptt_serial_line: Optional[SerialOutputLine] = None
    link_radio_ptt_polarity: Optional[Polarity] = None
    link_radio_tx_gain_db: Optional[float] = Field(default=None, ge=-40, le=20)
    link_radio_tx_ctcss_hz: Optional[float] = Field(default=None, ge=60, le=260)
    clear_link_radio_tx_ctcss_hz: bool = False
    link_radio_timeout: Optional[float] = Field(default=None, ge=30, le=1800)
    link_radio_courtesy_tone: Optional[bool] = None
    link_radio_mode: Optional[Literal["link", "remote_base"]] = None
    # --- facet: remote_base ---
    remote_base_rigctld: Optional[str] = Field(default=None, max_length=100, pattern=r"^$|^[A-Za-z0-9.\-]+(:[0-9]{1,5})?$|^\[[0-9A-Fa-f:]+\](:[0-9]{1,5})?$")
    remote_base_rig_mode: Optional[Literal["FM", "AM", "USB", "LSB"]] = None
    remote_base_mhz: Optional[float] = Field(default=None, ge=1, le=10000)
    remote_base_shift: Optional[Literal["simplex", "plus", "minus"]] = None
    remote_base_offset_mhz: Optional[float] = Field(default=None, gt=0, le=100)
    clear_remote_base_offset_mhz: bool = False
    remote_base_tone_hz: Optional[float] = None
    clear_remote_base_tone_hz: bool = False
    remote_base_ranges: Optional[str] = Field(default=None, max_length=300)
    remote_base_idle_minutes: Optional[float] = Field(default=None, ge=0, le=24 * 60)

    @field_validator("remote_base_ranges")
    @classmethod
    def _ranges(cls, value: Optional[str]) -> Optional[str]:
        if value is not None:
            parse_ranges(value)
        return value

    @field_validator("remote_base_tone_hz")
    @classmethod
    def _standard_tone(cls, value: Optional[float]) -> Optional[float]:
        if value is not None and not any(abs(value - tone) < 0.05 for tone in CTCSS_TONES):
            raise ValueError("not a standard CTCSS tone")
        return value
    link_radio_squelch_tail_ms: Optional[float] = Field(default=None, ge=0, le=300)
    link_radio_tx_delay_ms: Optional[float] = Field(default=None, ge=0, le=500)
    vox_hold: Optional[float] = Field(default=None, ge=0, le=5)
    vox_dtmf_hold: Optional[float] = Field(default=None, ge=0, le=10)
    tx_gain_db: Optional[float] = Field(default=None, ge=-40, le=20)
    tx_ctcss_hz: Optional[float] = Field(default=None, ge=60, le=260)
    clear_tx_ctcss_hz: bool = False
    tx_ctcss_level_db: Optional[float] = Field(default=None, ge=-40, le=-6)
    record_transmissions: Optional[bool] = None
    recording_retention_days: Optional[float] = Field(default=None, ge=0.1, le=365)
    # --- facet: autopatch ---
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
    # --- facet: backup ---
    backup_enabled: Optional[bool] = None
    backup_interval_hours: Optional[float] = Field(default=None, ge=1, le=720)
    backup_keep: Optional[int] = Field(default=None, ge=1, le=100)
    backup_include_recordings: Optional[bool] = None
    # --- facet: gpio_fan ---
    gpio_pins: Optional[dict[GpioPinNumber, GpioPinConfig]] = None
    gpio_schedules: Optional[list[GpioSchedule]] = Field(default=None, max_length=50)
    fan_output: Optional[FanOutput] = None
    fan_cm108_pin: Optional[int] = Field(default=None, ge=1, le=8)
    fan_gpio_pin: Optional[PiGpioPin] = None
    fan_polarity: Optional[Polarity] = None
    fan_run_on_minutes: Optional[float] = Field(default=None, ge=0, le=60)
    fan_temp_c: Optional[float] = Field(default=None, ge=40, le=90)
    clear_fan_temp_c: bool = False
    # --- facet: sensors ---
    sensors_enabled: Optional[bool] = None
    sensors_poll_interval: Optional[float] = Field(default=None, ge=5, le=3600)
    site_sensors: Optional[list[SiteSensor]] = Field(default=None, max_length=32)
    # --- facet: linking ---
    link_favorites: Optional[list[LinkFavorite]] = Field(default=None, max_length=50)
    link_schedules: Optional[list[LinkSchedule]] = Field(default=None, max_length=50)
    gmrs_mode: Optional[bool] = None
    # --- facet: net ---
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
    # --- facet: homeassistant ---
    homeassistant_url: Optional[str] = Field(default=None, max_length=300, pattern=r"^$|^https?://[^\s]+$")
    homeassistant_say_result: Optional[bool] = None
    # --- facet: public_page ---
    public_page_mode: Optional[PublicPageMode] = None
    public_page_text: Optional[str] = Field(default=None, max_length=500)
    public_page_audio: Optional[bool] = None
    public_page_max_listeners: Optional[int] = Field(default=None, ge=1, le=200)
    # --- facet: mailbox ---
    mailbox_enabled: Optional[bool] = None
    mailbox_leave_code: Optional[str] = Field(default=None, pattern=r"^[0-9A-D*]{1,8}$")
    mailbox_play_code: Optional[str] = Field(default=None, pattern=r"^[0-9A-D*]{1,8}$")
    mailbox_delete_code: Optional[str] = Field(default=None, pattern=r"^[0-9A-D*]{1,8}$")
    mailbox_max_seconds: Optional[float] = Field(default=None, ge=5, le=180)
    mailbox_retention_days: Optional[float] = Field(default=None, ge=1, le=365)
    mailbox_reminder_minutes: Optional[float] = Field(default=None, ge=0, le=24 * 60)
    # --- facet: transcription ---
    transcription_engine: Optional[TranscriptionEngine] = None
    transcription_url: Optional[str] = Field(default=None, max_length=300, pattern=r"^(https?://\S+)?$")
    transcription_model: Optional[str] = Field(default=None, min_length=1, max_length=100)

    @model_validator(mode="after")
    def _net_link_needs_a_node(self) -> "ConfigUpdateRequest":
        if self.net_links == "connect" and self.net_link_node == "":
            raise ValueError("choose the node to link for nets")
        return self

    @field_validator("courtesy_tone_custom")
    @classmethod
    def _valid_custom_tone(cls, text: Optional[str]) -> Optional[str]:
        if text is None:
            return None
        return " ".join(f"{hz}:{ms}" for hz, ms in parse_custom_tone(text))

    @field_validator("fan_cm108_pin")
    @classmethod
    def _fan_pin_not_ptt(cls, pin: Optional[int]) -> Optional[int]:
        if pin == 3:
            raise ValueError("GPIO3 is the CM108's PTT pin")
        return pin

    @field_validator("site_sensors")
    @classmethod
    def _unique_sensor_ids(cls, sensors: Optional[list[SiteSensor]]) -> Optional[list[SiteSensor]]:
        if sensors is None:
            return None
        ids = [s.id for s in sensors]
        if len(ids) != len(set(ids)):
            raise ValueError("each site sensor needs a unique id")
        return sensors
