"""Settings that change while a mode is on: net mode, and GMRS mode.

The saved configuration stays as the user left it; `effective_config`
layers the modes over it to give the settings actually in force, so ending
a net puts everything back without anyone having to remember what it was.
"""
from __future__ import annotations

import dataclasses

from .state_machine import RepeaterConfig

# 47 CFR 95.1751: a GMRS station identifies at least every 15 minutes while
# transmitting, and at the end of a series of transmissions.
GMRS_MAX_ID_INTERVAL = 900.0


def effective_config(saved: RepeaterConfig, net_active: bool = False) -> RepeaterConfig:
    changes: dict = {}
    if net_active:
        changes.update(tot_duration=saved.net_tot_duration, hang_time=saved.net_hang_time)
        if saved.net_courtesy_tone_style != "same" or saved.net_courtesy_tone_asset_id:
            style = saved.courtesy_tone_style if saved.net_courtesy_tone_style == "same" else saved.net_courtesy_tone_style
            # One tone for everyone during the net, whoever unkeyed.
            changes.update(
                courtesy_tone_style=style,
                courtesy_tone_asset_id=saved.net_courtesy_tone_asset_id,
                courtesy_tone_link_style="same",
                courtesy_tone_link_asset_id=None,
                courtesy_tone_patch_style="same",
                courtesy_tone_patch_asset_id=None,
            )
        if saved.net_hold_autopatch:
            changes.update(autopatch_enabled=False)
    if saved.gmrs_mode:
        # 95.1749: no telephone connection. 95.1733(a)(8)-(9): nothing carried
        # over a wireline link, nor sent to amateur stations, which AllStarLink,
        # EchoLink and APRS-IS are networks of.
        changes.update(
            id_interval=min(saved.id_interval, GMRS_MAX_ID_INTERVAL),
            autopatch_enabled=False,
            aprs_enabled=False,
            aprs_map_enabled=False,
        )
    return dataclasses.replace(saved, **changes) if changes else saved


def held_reason(saved: RepeaterConfig, net_active: bool, feature: str) -> str:
    """Why `feature` ("autopatch", "links" or "aprs") is off although it's
    switched on, or "" if it isn't held."""
    if saved.gmrs_mode and feature in ("autopatch", "links", "aprs"):
        return {
            "autopatch": "The phone patch is off in GMRS mode.",
            "links": "Linking is off in GMRS mode.",
            "aprs": "APRS is off in GMRS mode.",
        }[feature]
    if net_active and feature == "autopatch" and saved.net_hold_autopatch:
        return "The phone patch is off until the net ends."
    return ""
