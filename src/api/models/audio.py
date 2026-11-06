"""Audio devices, boards, levels, and engine API models."""
from __future__ import annotations

from typing import Literal, Optional, Any

from pydantic import BaseModel, Field

from .config import ConfigResponse

class AudioPreviewRequest(BaseModel):
    """Render `clip` as it would sound with `config` (unsaved dashboard
    edits) layered over the saved configuration."""

    clip: str = Field(min_length=1)
    config: dict[str, Any] = {}
    net: bool = False  # as it would sound during a net


class TTSInfoResponse(BaseModel):
    engine: Optional[str]

class AudioDeviceResponse(BaseModel):
    name: str
    inputs: int
    outputs: int
    default_samplerate: float
    card: Optional[int] = None
    stable_id: Optional[str] = None


class SerialPortResponse(BaseModel):
    path: str
    target: str  # the device a /dev/serial/by-id name points at


class BoardResponse(BaseModel):
    id: str
    name: str
    maker: str
    kind: Literal["usb", "pi"]
    notes: str
    device_hints: list[str]
    two_port: bool
    mixer: list[tuple[str, str]]
    unsupported: Optional[str]


class BoardApplyRequest(BaseModel):
    input_device: Optional[str] = Field(default=None, max_length=200)
    output_device: Optional[str] = Field(default=None, max_length=200)
    link_input_device: Optional[str] = Field(default=None, max_length=200)
    link_output_device: Optional[str] = Field(default=None, max_length=200)
    set_mixer: bool = True


class MixerResultResponse(BaseModel):
    card: int
    control: str
    value: str
    error: Optional[str]


class BoardApplyResponse(BaseModel):
    config: ConfigResponse
    mixer: list[MixerResultResponse]
    # Sound devices whose ALSA card couldn't be found, so their levels weren't set.
    mixer_skipped: list[str]


class SoundLevelResponse(BaseModel):
    side: Literal["input", "output", "link_input", "link_output"]
    card: int
    control: str
    min: int
    max: int
    value: int
    db: Optional[float]
    saved: bool  # set again whenever the engine opens the card
    kind: Literal["volume", "switch"] = "volume"  # ALSA switch (e.g. Auto Gain Control)


class SoundLevelsResponse(BaseModel):
    levels: list[SoundLevelResponse]
    # Why a side has no levels: the system default device, or amixer failing.
    notes: list[str]


class SoundLevelUpdateRequest(BaseModel):
    side: Literal["input", "output", "link_input", "link_output"]
    control: str = Field(min_length=1, max_length=60)
    value: int = Field(ge=0, le=100000)


class LinkAudioResponse(BaseModel):
    configured: bool
    running: bool
    error: Optional[str]
    node: Optional[str]
    keyed: bool

class ReceiverOpeningResponse(BaseModel):
    started_at: float
    duration: float
    open_db: float
    peak_db: float
    strongest_hz: Optional[float]
    tone_share: float
    ctcss_hz: Optional[float]
    dtmf_digits: str
    after_tx: Optional[float]  # 0 = while the repeater was transmitting; None = not in the last minute


class AudioGlitchResponse(BaseModel):
    at: float
    last_at: float  # the same glitch repeating within a second joins one entry
    counter: Literal["dropped_input_blocks", "starved_output_blocks", "input_overflows", "output_underflows"]
    count: int
    state: str  # the controller's state
    transmitting: bool


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
    input_overflows: int = 0
    output_underflows: int = 0
    hardware_ptt: Optional[str]  # "cm108", "gpio" or "serial"
    listeners: int = 0
