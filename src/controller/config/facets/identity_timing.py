from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional
from controller.config.types import CourtesyStyle, CourtesyVariant
"""IdentityTimingConfig: config facet (flat wire field names)."""


@dataclass(frozen=True)
class IdentityTimingConfig:
    courtesy_tone_duration: float = 0.2
    hang_time: float = 3.0
    tot_duration: float = 180.0
    id_interval: float = 600.0
    # Off: ID only while there's been activity since the last one. On: ID every
    # interval around the clock, like a beacon.
    idle_id: bool = False
    # "simplex": one radio on one frequency, a node for the links. Local users
    # aren't repeated; the transmitter keys for linked stations, IDs, clips and
    # courtesy tones, and never over a signal on the channel.
    node_mode: Literal['repeater', 'simplex'] = 'repeater'
    # after a local user, on a simplex node
    simplex_courtesy_tone: bool = True  # after a local user, on a simplex node
    # A transmission shorter than this (a kerchunk) doesn't make an ID owed; 0 = every one does.
    id_skip_short_seconds: float = 0.0
    # The long ID takes the place of the regular one once long_id_interval has
    # passed since the last long ID: a fuller message, less often.
    # passed since the last long ID: a fuller message, less often.
    long_id_mode: Literal['off', 'voice', 'both'] = 'off'  # "both": voice, then CW
    long_id_interval: float = 3600.0
    long_id_text: str = 'This is {callsign}. The time is {time}.'
    long_id_asset_id: Optional[str] = None
    id_audio_duration: float = 3.0
    require_ctcss_hz: Optional[float] = None
    # How a local user brings the repeater up. "carrier": carrier, plus the
    # CTCSS tone on every transmission if require_ctcss_hz is set. "ctcss_open":
    # the tone opens it, then carrier alone works until it drops to idle.
    # "tone_burst": a 1750 Hz burst opens it, then carrier until it drops.
    access_mode: Literal['carrier', 'ctcss_open', 'tone_burst'] = 'carrier'
    # shortest 1750 Hz burst that counts
    tone_burst_ms: float = 300.0  # shortest 1750 Hz burst that counts
    # carrier must last this long before keying up from idle; 0 = off
    kerchunk_delay: float = 0.0  # carrier must last this long before keying up from idle; 0 = off
    # Stuck-carrier lockout: this many timeouts within lockout_window (a
    # carrier that never drops times out again every tot_duration) stops all
    # repeating until the channel has been quiet for lockout_clear_after.
    # IDs still go out. 0 = off.
    lockout_timeouts: int = 3
    lockout_window: float = 900.0
    lockout_clear_after: float = 60.0
    # Off: nothing transmits (no repeat, IDs or announcements), but DTMF is
    # still decoded so a control operator can turn it back on over the air.
    transmitter_enabled: bool = True
    callsign: str = ''
    id_mode: Literal['voice', 'cw', 'both'] = 'voice'
    cw_wpm: float = 20.0
    cw_tone_hz: float = 700.0
    # sent after the callsign in CW, e.g. "/R"
    cw_id_suffix: str = ''  # sent after the callsign in CW, e.g. "/R"
    courtesy_tone_asset_id: Optional[str] = None
    id_asset_id: Optional[str] = None
    timeout_tone_asset_id: Optional[str] = None
    courtesy_tone_style: CourtesyStyle = 'beep'
    # Different tones after a linked station (AllStarLink/EchoLink) and at the
    # end of a phone call, so listeners can tell; "same" uses the one above.
    courtesy_tone_link_style: CourtesyVariant = 'same'
    courtesy_tone_link_asset_id: Optional[str] = None
    courtesy_tone_patch_style: CourtesyVariant = 'same'
    courtesy_tone_patch_asset_id: Optional[str] = None
    # the "custom" style: up to 4 hz:ms segments, 0 Hz = gap
    courtesy_tone_custom: str = '880:100 0:40 1320:100'  # the "custom" style: up to 4 hz:ms segments, 0 Hz = gap
    voice_id_text: str = '{callsign} repeater'
    id_phonetic: bool = False
    tts_voice: str = ''
    # Kokoro speech rate (0.5–2.0). Ignored by engines that have no speed control.
    tts_speed: float = 1.0

# Optional fields the dashboard may null out via clear_<name> on ConfigUpdateRequest.
CLEAR_FIELDS = ('long_id_asset_id', 'require_ctcss_hz', 'courtesy_tone_asset_id', 'id_asset_id', 'timeout_tone_asset_id', 'courtesy_tone_link_asset_id', 'courtesy_tone_patch_asset_id')

