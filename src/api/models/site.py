"""Site ops: audit, assets, nets, control codes, HA, stream, mailbox, hub."""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

from .types import HOMEASSISTANT_TARGET, TranscriptionEngine

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

class HomeAssistantStatus(BaseModel):
    token_set: bool


class HomeAssistantTestRequest(BaseModel):
    target: str = Field(pattern=HOMEASSISTANT_TARGET)


class PublicStatus(BaseModel):
    """What /listen shows."""

    callsign: str
    text: str
    on_air: bool
    receiving: bool
    net: Optional[str]
    audio: bool
    listeners: int
    max_listeners: int
    username: Optional[str] = None  # who's signed in, for a Sign out button


class StreamSettingsRequest(BaseModel):
    enabled: bool = False
    host: str = Field(default="", max_length=200, pattern=r"^$|^[A-Za-z0-9.\-]+$")
    port: int = Field(default=80, ge=1, le=65535)
    mount: str = Field(default="", max_length=200, pattern=r"^$|^/?[A-Za-z0-9_.\-/]+$")
    username: str = Field(default="source", min_length=1, max_length=100)
    password: Optional[str] = Field(default=None, max_length=200)  # None or "" keeps the saved one
    name: str = Field(default="", max_length=100)
    description: str = Field(default="", max_length=200)
    genre: str = Field(default="", max_length=100)
    bitrate: Literal[16, 24, 32] = 16
    legacy_icecast: bool = False


class StreamStatus(BaseModel):
    state: Literal["off", "connecting", "streaming", "retrying", "error"]
    detail: str
    since: float


class StreamResponse(BaseModel):
    enabled: bool
    host: str
    port: int
    mount: str
    username: str
    password_set: bool
    name: str
    description: str
    genre: str
    bitrate: int
    legacy_icecast: bool
    status: StreamStatus


class MailboxBoxRequest(BaseModel):
    name: str = Field(default="", max_length=60)
    pin: Optional[str] = Field(default=None, pattern=r"^[0-9]{4,8}$")  # None keeps the current PIN


class MailboxBoxResponse(BaseModel):
    box: str
    name: str
    pin_set: bool
    messages: int


class MailboxMessageResponse(BaseModel):
    id: str
    box: str
    left_at: datetime
    duration: float
    transcript: Optional[str] = None
    callsigns: list[str] = []


class TranscriptionStatus(BaseModel):
    engine: TranscriptionEngine
    vosk_installed: bool
    vosk_model: bool
    vosk_model_dir: str
    api_key_set: bool
    pending: int
    transcribed: int
    last_error: Optional[str]


class MailboxResponse(BaseModel):
    boxes: list[MailboxBoxResponse]
    messages: list[MailboxMessageResponse]


class LinkRadioStatus(BaseModel):
    enabled: bool
    running: bool
    error: Optional[str]
    name: str
    rx_level_db: float
    receiving: bool
    transmitting: bool
    timed_out: bool
    id_owed: bool
    device_sample_rate: Optional[int]
    dropped_input_blocks: int
    starved_output_blocks: int
    mode: Literal["link", "remote_base"] = "link"
    rig_error: Optional[str] = None  # remote base CAT control
    tuned: bool = False  # the radio was set to the saved frequency, shift and tone


class MonitorReceiverStatus(BaseModel):
    enabled: bool
    running: bool
    error: Optional[str]
    name: str
    level_db: float
    squelch_open: bool
    device_sample_rate: Optional[int]
    sample_rate: int
    dropped_input_blocks: int
    listeners: int


# --- multi-repeater hub (Sites view) -----------------------------------------


class HubSiteResponse(BaseModel):
    id: str
    name: str
    base_url: str
    dashboard_url: str
    token_set: bool


class HubSiteCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    base_url: str = Field(min_length=1, max_length=300)
    api_token: str = Field(min_length=1, max_length=200)


class HubSiteUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=80)
    base_url: Optional[str] = Field(default=None, min_length=1, max_length=300)
    api_token: Optional[str] = Field(default=None, min_length=1, max_length=200)


class HubSiteStatusResponse(BaseModel):
    id: str
    name: str
    base_url: str
    dashboard_url: str
    reachable: bool
    polled_at: float
    error: Optional[str] = None
    callsign: Optional[str] = None
    state: Optional[str] = None
    version: Optional[str] = None
    channel: Optional[str] = None
    last_activity: Optional[float] = None
    alerts_summary: Optional[str] = None
