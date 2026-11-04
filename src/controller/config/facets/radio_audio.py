from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional
"""RadioAudioConfig: config facet (flat wire field names)."""


@dataclass(frozen=True)
class RadioAudioConfig:
    # flat (discriminator) receive audio: de-emphasize it
    rx_deemphasis: bool = False  # flat (discriminator) receive audio: de-emphasize it
    # flat transmit input: pre-emphasize what's sent
    tx_preemphasis: bool = False  # flat transmit input: pre-emphasize what's sent
    audio_enabled: bool = False
    # the api.boards preset last applied; "" = set up by hand
    board_preset: str = ''  # the api.boards preset last applied; "" = set up by hand
    # the dashboard's first-run wizard was finished or skipped
    setup_wizard_done: bool = False  # the dashboard's first-run wizard was finished or skipped
    # PortAudio device name; "" = system default
    audio_input_device: str = ''  # PortAudio device name; "" = system default
    audio_output_device: str = ''
    # Operator notes for the main radio interface (dashboard only).
    # Operator notes for the main radio interface (dashboard only).
    radio_model: str = ''  # "Yaesu DR-1X", "two mobiles + AIOC"
    # free text: "146.94 / 146.34", "GMRS ch 20"
    radio_frequency: str = ''  # free text: "146.94 / 146.34", "GMRS ch 20"
    radio_notes: str = ''
    # api.mixer: mixer control -> volume step, set on the input card (capture) and output card (playback)
    audio_input_levels: dict = field(default_factory=dict)
    audio_output_levels: dict = field(default_factory=dict)
    # "gpio" = a Raspberry Pi header pin; "serial" = a serial port's CTS, DSR or DCD
    cos_source: Literal['vox', 'ctcss', 'cm108', 'gpio', 'serial'] = 'vox'
    # hardware COS: the level meaning "carrier" (serial: high = asserted)
    cos_polarity: Literal['low', 'high'] = 'low'  # hardware COS: the level meaning "carrier" (serial: high = asserted)
    # BCM numbers; GPIO27 is header pin 13
    cos_gpio_pin: int = 27  # BCM numbers; GPIO27 is header pin 13
    # /dev/ttyUSB0, /dev/serial/by-id/...
    cos_serial_device: str = ''  # /dev/ttyUSB0, /dev/serial/by-id/...
    cos_serial_line: Literal['cts', 'dsr', 'dcd'] = 'cts'
    # "cm108" keys a CM108 interface's PTT, if one is set up
    ptt_output: Literal['cm108', 'gpio', 'serial'] = 'cm108'  # "cm108" keys a CM108 interface's PTT, if one is set up
    # header pin 11
    ptt_gpio_pin: int = 17  # header pin 11
    ptt_serial_device: str = ''
    ptt_serial_line: Literal['rts', 'dtr'] = 'rts'
    # the level that keys the transmitter
    ptt_polarity: Literal['high', 'low'] = 'high'  # the level that keys the transmitter
    vox_threshold_db: float = -40.0
    # cut from the end of each received transmission (the noise burst)
    squelch_tail_ms: float = 0.0  # cut from the end of each received transmission (the noise burst)
    # keyed, but silent, for this long before any audio
    tx_delay_ms: float = 0.0  # keyed, but silent, for this long before any audio
    # DTMF tones are decoded but never passed on
    dtmf_mute: bool = True  # DTMF tones are decoded but never passed on
    vox_hold: float = 0.4
    # VOX hold when the quiet follows a DTMF digit
    vox_dtmf_hold: float = 3.0  # VOX hold when the quiet follows a DTMF digit
    tx_gain_db: float = 0.0
    # sub-audible tone added to everything transmitted
    tx_ctcss_hz: Optional[float] = None  # sub-audible tone added to everything transmitted
    tx_ctcss_level_db: float = -20.0
    record_transmissions: bool = False
    recording_retention_days: float = 7.0

# Optional fields the dashboard may null out via clear_<name> on ConfigUpdateRequest.
CLEAR_FIELDS = ('tx_ctcss_hz',)

