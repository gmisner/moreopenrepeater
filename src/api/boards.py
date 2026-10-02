"""Interface board presets: the PTT/COS wiring and mixer levels of common
radio interfaces, so a new station doesn't have to look up pin numbers.

The presets live in boards.json next to this file. Each one is a set of
config fields for the repeater port (and, on two-port boards, the link
radio port) plus ALSA mixer levels applied with `amixer`.
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

BOARDS_PATH = Path(__file__).with_name("boards.json")
AMIXER_TIMEOUT_SECONDS = 5.0

# Changing any of these by hand means the station no longer matches its preset.
WIRING_FIELDS = frozenset({
    "cos_source", "cos_polarity", "cos_gpio_pin", "ptt_output", "ptt_gpio_pin", "ptt_polarity",
    "link_radio_cos", "link_radio_cos_polarity", "link_radio_cos_gpio_pin",
    "link_radio_ptt", "link_radio_ptt_gpio_pin", "link_radio_ptt_polarity",
})

# PortAudio's ALSA device names end in "(hw:<card>,<device>)".
_HW_CARD = re.compile(r"\(hw:(\d+),\d+\)")


@dataclass(frozen=True)
class Board:
    id: str
    name: str
    maker: str
    kind: str  # "usb" or "pi"
    notes: str = ""
    device_hints: tuple[str, ...] = ()
    repeater: dict = field(default_factory=dict)
    link: Optional[dict] = None
    mixer: tuple[tuple[str, str], ...] = ()
    unsupported: Optional[str] = None


@dataclass(frozen=True)
class MixerResult:
    card: int
    control: str
    value: str
    error: Optional[str] = None


def load_boards(path: Path = BOARDS_PATH) -> list[Board]:
    return [
        Board(
            **{
                **entry,
                "device_hints": tuple(entry.get("device_hints", ())),
                "mixer": tuple(tuple(pair) for pair in entry.get("mixer", ())),
            }
        )
        for entry in json.loads(path.read_text())
    ]


def preset_changes(
    board: Board,
    input_device: Optional[str] = None,
    output_device: Optional[str] = None,
    link_input_device: Optional[str] = None,
    link_output_device: Optional[str] = None,
) -> dict:
    """Config overrides that set a station up for `board`. The link radio
    is only turned on when both of its sound devices are given."""
    changes = {"audio_enabled": True, "board_preset": board.id, **board.repeater}
    if input_device is not None:
        changes["audio_input_device"] = input_device
    if output_device is not None:
        changes["audio_output_device"] = output_device
    if board.link:
        changes.update(board.link)
        if link_input_device and link_output_device:
            changes.update(
                link_radio_enabled=True,
                link_radio_input_device=link_input_device,
                link_radio_output_device=link_output_device,
            )
    return changes


def alsa_card(device_name: str) -> Optional[int]:
    """The ALSA card number in a PortAudio device name, or None (e.g. the
    system default device)."""
    match = _HW_CARD.search(device_name or "")
    return int(match.group(1)) if match else None


def run_amixer(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=AMIXER_TIMEOUT_SECONDS)


def apply_mixer(
    cards: list[int], settings: tuple[tuple[str, str], ...], run: Callable[[list[str]], subprocess.CompletedProcess] = run_amixer
) -> list[MixerResult]:
    """Set each control on each card. A control the card doesn't have is
    reported, not fatal: USB interfaces name their controls differently."""
    results = []
    for card in cards:
        for control, value in settings:
            try:
                done = run(["amixer", "-q", "-c", str(card), "sset", control, *value.split()])
                error = None if done.returncode == 0 else (done.stderr.strip().splitlines() or ["amixer failed"])[-1]
            except FileNotFoundError:
                error = "amixer isn't installed (sudo apt install alsa-utils)"
            except subprocess.TimeoutExpired:
                error = "amixer didn't respond"
            results.append(MixerResult(card, control, value, error))
    return results
