from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional
"""LinkRadioConfig: config facet (flat wire field names)."""


@dataclass(frozen=True)
class LinkRadioConfig:
    # A second radio linking the repeater to a far station (api.link_radio).
    link_radio_enabled: bool = False
    # "Link to W1XYZ"
    link_radio_name: str = ''  # "Link to W1XYZ"
    # Operator notes only (dashboard); not used by the audio path.
    # Operator notes only (dashboard); not used by the audio path.
    link_radio_model: str = ''  # "Baofeng UV-5R", "Digirig + IC-2730"
    # free text: "446.000", "146.94 / 146.34"
    link_radio_frequency: str = ''  # free text: "446.000", "146.94 / 146.34"
    link_radio_notes: str = ''
    link_radio_input_device: str = ''
    link_radio_output_device: str = ''
    # Same for the link radio's own USB interface (separate Digirig / CM108).
    link_radio_input_levels: dict = field(default_factory=dict)
    link_radio_output_levels: dict = field(default_factory=dict)
    # "cm108": a second CM108
    link_radio_cos: Literal['vox', 'ctcss', 'cm108', 'gpio', 'serial'] = 'vox'  # "cm108": a second CM108
    link_radio_cos_polarity: Literal['low', 'high'] = 'low'
    # header pin 16
    link_radio_cos_gpio_pin: int = 23  # header pin 16
    link_radio_cos_serial_device: str = ''
    link_radio_cos_serial_line: Literal['cts', 'dsr', 'dcd'] = 'cts'
    link_radio_vox_threshold_db: float = -40.0
    # "none": the radio keys on audio (its own VOX)
    link_radio_ptt: Literal['cm108', 'gpio', 'serial', 'none'] = 'gpio'  # "none": the radio keys on audio (its own VOX)
    # header pin 18
    link_radio_ptt_gpio_pin: int = 24  # header pin 18
    link_radio_ptt_serial_device: str = ''
    link_radio_ptt_serial_line: Literal['rts', 'dtr'] = 'rts'
    link_radio_ptt_polarity: Literal['high', 'low'] = 'high'
    link_radio_tx_gain_db: float = 0.0
    # for a far repeater that needs a tone
    link_radio_tx_ctcss_hz: Optional[float] = None  # for a far repeater that needs a tone
    link_radio_timeout: float = 180.0
    link_radio_squelch_tail_ms: float = 0.0
    link_radio_tx_delay_ms: float = 0.0
    link_radio_courtesy_tone: bool = True
    # A remote base instead of a link (controller.remote_base, api.remote_base):
    # users switch the link radio on and tune it by DTMF, through Hamlib's rigctld.
    link_radio_mode: Literal['link', 'remote_base'] = 'link'

# Optional fields the dashboard may null out via clear_<name> on ConfigUpdateRequest.
CLEAR_FIELDS = ('link_radio_tx_ctcss_hz',)

