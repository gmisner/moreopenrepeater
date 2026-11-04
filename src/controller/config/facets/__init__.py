"""Config facets: grouped frozen dataclasses composing RepeaterConfig."""
from __future__ import annotations

from .identity_timing import IdentityTimingConfig
from .radio_audio import RadioAudioConfig
from .link_radio import LinkRadioConfig
from .remote_base import RemoteBaseConfig
from .aprs import AprsConfig
from .weather import WeatherConfig
from .monitor import MonitorConfig
from .autopatch import AutopatchConfig
from .mailbox import MailboxConfig
from .net import NetConfig
from .gpio_fan import GpioFanConfig
from .homeassistant import HomeassistantConfig
from .linking import LinkingConfig
from .backup import BackupConfig
from .transcription import TranscriptionConfig
from .public_page import PublicPageConfig

FACET_CLASSES = (
    ('identity_timing', IdentityTimingConfig),
    ('radio_audio', RadioAudioConfig),
    ('link_radio', LinkRadioConfig),
    ('remote_base', RemoteBaseConfig),
    ('aprs', AprsConfig),
    ('weather', WeatherConfig),
    ('monitor', MonitorConfig),
    ('autopatch', AutopatchConfig),
    ('mailbox', MailboxConfig),
    ('net', NetConfig),
    ('gpio_fan', GpioFanConfig),
    ('homeassistant', HomeassistantConfig),
    ('linking', LinkingConfig),
    ('backup', BackupConfig),
    ('transcription', TranscriptionConfig),
    ('public_page', PublicPageConfig),
)

__all__ = [
    'IdentityTimingConfig',
    'RadioAudioConfig',
    'LinkRadioConfig',
    'RemoteBaseConfig',
    'AprsConfig',
    'WeatherConfig',
    'MonitorConfig',
    'AutopatchConfig',
    'MailboxConfig',
    'NetConfig',
    'GpioFanConfig',
    'HomeassistantConfig',
    'LinkingConfig',
    'BackupConfig',
    'TranscriptionConfig',
    'PublicPageConfig',
    'FACET_CLASSES',
]
