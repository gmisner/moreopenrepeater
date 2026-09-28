"""Pydantic request/response models for the repeater API."""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


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
    callsign: str
    id_mode: Literal["voice", "cw", "both"]
    cw_wpm: float
    cw_tone_hz: float
    courtesy_tone_asset_id: Optional[str]
    id_asset_id: Optional[str]
    timeout_tone_asset_id: Optional[str]
    aprs_enabled: bool
    aprs_server: str
    aprs_port: int
    aprs_callsign: str
    aprs_lat: Optional[float]
    aprs_lon: Optional[float]
    aprs_comment: str
    aprs_beacon_interval: float


class ConfigUpdateRequest(BaseModel):
    courtesy_tone_duration: Optional[float] = Field(default=None, gt=0)
    hang_time: Optional[float] = Field(default=None, gt=0)
    tot_duration: Optional[float] = Field(default=None, gt=0)
    id_interval: Optional[float] = Field(default=None, gt=0)
    id_audio_duration: Optional[float] = Field(default=None, gt=0)
    require_ctcss_hz: Optional[float] = None
    clear_require_ctcss_hz: bool = False
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


class SnapshotModel(BaseModel):
    config: ConfigResponse
    macros: list[MacroResponse]


class SnapshotImportRequest(BaseModel):
    """A backup file. `config` is loosely typed so a backup from an older
    version (missing newer settings) still restores -- missing fields get
    defaults, then the merged result is validated as a ConfigResponse."""

    config: dict[str, Any]
    macros: list[MacroCreateRequest] = []


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
