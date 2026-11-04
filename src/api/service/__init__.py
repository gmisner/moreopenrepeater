"""RepeaterService facade and feature collaborators."""
from playout.tts import spoken_time

from .facade import RepeaterService
from .protocols import AudioOutput, PatchCalls
from .snapshot import (
    LINK_AUDIO_NODE,
    LINK_RADIO_NODE,
    MAX_HELD_ANNOUNCEMENTS,
    StatusSnapshot,
    WEATHER_SUMMARY_MAX,
    announcements_from_snapshot,
    config_from_snapshot,
    macros_from_snapshot,
    talking_clock_text,
)

__all__ = [
    "AudioOutput",
    "LINK_AUDIO_NODE",
    "LINK_RADIO_NODE",
    "MAX_HELD_ANNOUNCEMENTS",
    "PatchCalls",
    "RepeaterService",
    "StatusSnapshot",
    "WEATHER_SUMMARY_MAX",
    "announcements_from_snapshot",
    "config_from_snapshot",
    "macros_from_snapshot",
    "spoken_time",
    "talking_clock_text",
]
