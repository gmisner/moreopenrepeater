"""AllStar, GPIO, sensors, links, and EchoLink API models."""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

from .audio import LinkAudioResponse
from .types import FanOutput, NodeNumber, _CONF_TEXT

class AllStarNodeInfo(BaseModel):
    number: str
    rxchannel: str
    duplex: str
    controlled: bool


class AllStarIdentityInfo(BaseModel):
    node: str
    callsign: str
    has_password: bool


class AllStarStatusResponse(BaseModel):
    available: bool
    manual: bool
    can_restart: bool
    restart_needed: bool
    error: Optional[str]
    nodes: list[AllStarNodeInfo]
    node: Optional[str]
    identity: Optional[AllStarIdentityInfo] = None
    audio: LinkAudioResponse


class GpioPinStatus(BaseModel):
    pin: int
    name: str
    mode: Optional[Literal["output", "input", "fan"]]
    on: Optional[bool]
    until: Optional[datetime] = None  # a scheduled output's switch-off, local time


class GpioScheduleStatus(BaseModel):
    pin: int
    next_start: Optional[datetime]
    until: Optional[datetime]


class FanStatus(BaseModel):
    output: FanOutput
    pin: Optional[int]
    on: Optional[bool]
    reason: Optional[Literal["transmitting", "run-on", "hot"]]
    run_on_left: Optional[float]  # seconds
    temperature_c: Optional[float]
    error: Optional[str]


class SensorHistoryPoint(BaseModel):
    at: float
    value: float


class SensorStatus(BaseModel):
    id: str
    name: str
    type: str
    units: str
    scale: float = 1.0
    offset: float = 0.0
    high: Optional[float] = None
    low: Optional[float] = None
    hysteresis: float = 0.5
    alert: bool = True
    speak_on_alert: bool = False
    value: Optional[float] = None
    at: Optional[float] = None
    alert_state: Optional[Literal["high", "low"]] = None
    error: Optional[str] = None
    history: list[SensorHistoryPoint] = []


class SensorsStatusResponse(BaseModel):
    enabled: bool
    poll_interval: float
    sensors: list[SensorStatus]


class GpioStatusResponse(BaseModel):
    available: bool
    error: Optional[str]
    pins: list[GpioPinStatus]
    schedules: list[GpioScheduleStatus] = []
    fan: Optional[FanStatus] = None


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


class LinkEvent(BaseModel):
    at: datetime
    kind: Literal["connected", "disconnected", "keyed", "unkeyed"]
    node: str
    label: str = ""


class LinkEventsResponse(BaseModel):
    events: list[LinkEvent]


class LinkConnectRequest(BaseModel):
    node: NodeNumber
    monitor: bool = False


class AllStarNodeRequest(BaseModel):
    node: str = Field(pattern=r"^[0-9]{1,10}$")


class AllStarIdentityRequest(BaseModel):
    from_node: str = Field(pattern=r"^[0-9]{1,10}$")
    node: str = Field(pattern=r"^[0-9]{1,10}$")
    callsign: str = Field(min_length=3, max_length=10, pattern=r"^[A-Za-z0-9]{3,10}$")
    password: str = Field(default="", max_length=64, pattern=r"^[0-9A-Za-z_-]*$")


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


class DmrStationInfo(BaseModel):
    callsign: str
    dmr_id: str
    repeater_ssid: str
    has_password: bool
    network: Literal["brandmeister", "tgif", "custom"]
    host: str
    port: int
    talkgroup: int
    slot: int
    color_code: int
    location: str
    description: str
    use_emulator: bool


class DmrLastHeard(BaseModel):
    radio_id: int
    callsign: str
    name: str = ""
    talkgroup: int
    slot: int
    started_at: float
    ended_at: Optional[float] = None
    active: bool = False


class DmrAudioStatus(BaseModel):
    configured: bool
    running: bool
    error: Optional[str]
    peer: Optional[str]
    listen: Optional[str]
    keyed: bool
    last_heard: Optional[DmrLastHeard] = None


class DmrServicesStatus(BaseModel):
    analog_bridge: Optional[bool]
    mmdvm_bridge: Optional[bool]
    md380_emu: Optional[bool]


class DmrRestartStatus(BaseModel):
    available: bool
    requested: bool
    last_action: Optional[str] = None


class DmrStatusResponse(BaseModel):
    available: bool
    enabled: bool
    error: Optional[str]
    settings: Optional[DmrStationInfo]
    audio: DmrAudioStatus
    services: DmrServicesStatus
    restart: DmrRestartStatus


class DmrRequest(BaseModel):
    callsign: str = Field(min_length=3, max_length=10, pattern=r"^[A-Za-z0-9]{3,10}$")
    dmr_id: str = Field(pattern=r"^[0-9]{7}$")
    repeater_ssid: str = Field(default="01", pattern=r"^[0-9]{2}$")
    password: str = Field(default="", max_length=64, pattern=r"^[^\s;]*$")
    network: Literal["brandmeister", "tgif", "custom"] = "brandmeister"
    host: str = Field(default="", max_length=120, pattern=r"^[A-Za-z0-9._:-]*$")
    port: int = Field(default=62031, ge=1, le=65535)
    talkgroup: int = Field(default=9, ge=1, le=9999999)
    slot: int = Field(default=2, ge=1, le=2)
    color_code: int = Field(default=1, ge=0, le=15)
    location: str = Field(default="", max_length=80)
    description: str = Field(default="moreopenrepeater", max_length=80)
    use_emulator: bool = True


class DmrTalkgroupRequest(BaseModel):
    talkgroup: int = Field(ge=1, le=9_999_999)
    slot: Optional[int] = Field(default=None, ge=1, le=2)
