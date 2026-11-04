from __future__ import annotations

from dataclasses import dataclass
"""AutopatchConfig: config facet (flat wire field names)."""


@dataclass(frozen=True)
class AutopatchConfig:
    autopatch_enabled: bool = False
    autopatch_access_code: str = '*6'
    # "
    autopatch_hangup_code: str = '#'  # "
    # Asterisk channel to call; {number} is replaced with the dialed digits.
    # Asterisk channel to call; {number} is replaced with the dialed digits.
    autopatch_dial_string: str = 'PJSIP/{number}@mor-trunk'  # the trunk the dashboard sets up
    # "", "1" or "+1"; see controller.autopatch.format_number
    autopatch_ten_digit_prefix: str = ''  # "", "1" or "+1"; see controller.autopatch.format_number
    autopatch_caller_id: str = ''
    # dialplan patterns, see controller.autopatch
    autopatch_allowed: str = '911 NXXNXXXXXX'  # dialplan patterns, see controller.autopatch
    autopatch_blocked: str = '900XXXXXXX NXX976XXXX'
    autopatch_max_call_seconds: float = 180.0
    autopatch_ring_seconds: float = 30.0
    # answer calls to the phone line
    autopatch_incoming_enabled: bool = False  # answer calls to the phone line
    # callers key it in; calls aren't answered without one
    autopatch_incoming_pin: str = ''  # callers key it in; calls aren't answered without one
