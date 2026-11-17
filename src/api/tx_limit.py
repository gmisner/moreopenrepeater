"""How long the audio engine lets a transmitter stay keyed without a break.

The controller enforces the real limits (time-out timer, net time-out,
autopatch call length, link radio time-out). The engine's own cap is a
backstop for a controller that has stopped enforcing them, so it sits well
above all of them and must never fire in normal operation. See
`audio_io.engine.TX_LIMIT_MARGIN_SECONDS` and issue #246.
"""
from __future__ import annotations

from audio_io.engine import TX_LIMIT_MARGIN_SECONDS


def repeater_transmit_limit(config) -> float:
    """For the main radio: the longest of the controller's limits, plus a margin."""
    longest = max(
        config.tot_duration or 0.0,
        config.net_tot_duration or 0.0,
        config.autopatch_max_call_seconds or 0.0,
    )
    return longest + TX_LIMIT_MARGIN_SECONDS


def link_radio_transmit_limit(config) -> float:
    """For the link radio: its own time-out, plus a margin."""
    return (config.link_radio_timeout or 0.0) + TX_LIMIT_MARGIN_SECONDS
