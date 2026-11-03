"""Sound card levels: the ALSA mixer volumes of the repeater and link
radio interfaces, saved with the rest of the config and set again
whenever each audio engine opens its cards.

A USB interface forgets its levels when it's unplugged, and the service
can't run `alsactl store` (it isn't root), so the controller keeps them
itself: capture volumes on each radio's input card, and playback volumes
on each radio's output card. They're saved by control name, not card
number, so they follow the interface if ALSA numbers the cards differently
after a reboot.
"""
from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass
from typing import Callable, Literal, Optional

from .boards import alsa_card, run_amixer

_logger = logging.getLogger("moreopenrepeater.mixer")

# "input"/"output" = repeater; "link_input"/"link_output" = link radio.
Side = Literal["input", "output", "link_input", "link_output"]
Run = Callable[[list[str]], subprocess.CompletedProcess]

_CONTROL = re.compile(r"^Simple mixer control '(.+)',(\d+)$")
_LIMITS = re.compile(r"(Playback|Capture) (-?\d+) - (-?\d+)")
_READING = re.compile(r"(Playback|Capture) (-?\d+) \[\d+%\](?: \[(-?[\d.]+)dB\])?")

_SIDE_DEVICE = {
    "input": "audio_input_device",
    "output": "audio_output_device",
    "link_input": "link_radio_input_device",
    "link_output": "link_radio_output_device",
}
_SIDE_LEVELS = {
    "input": "audio_input_levels",
    "output": "audio_output_levels",
    "link_input": "link_radio_input_levels",
    "link_output": "link_radio_output_levels",
}
_SIDE_LABEL = {
    "input": "input",
    "output": "output",
    "link_input": "link radio input",
    "link_output": "link radio output",
}


@dataclass(frozen=True)
class Volume:
    control: str
    direction: Literal["playback", "capture"]
    min: int
    max: int
    value: int
    db: Optional[float]


def parse_volumes(text: str) -> list[Volume]:
    """The volumes in `amixer scontents`, one per control and direction."""
    volumes: list[Volume] = []
    name: Optional[str] = None
    limits: dict[str, tuple[int, int]] = {}
    readings: dict[str, tuple[int, Optional[float]]] = {}

    def finish() -> None:
        if name is None:
            return
        for direction in ("Playback", "Capture"):
            if direction in limits and direction in readings:
                low, high = limits[direction]
                value, db = readings[direction]
                volumes.append(Volume(name, direction.lower(), low, high, value, db))  # type: ignore[arg-type]

    for line in text.splitlines():
        header = _CONTROL.match(line)
        if header:
            finish()
            name = header.group(1) if header.group(2) == "0" else None
            limits, readings = {}, {}
            continue
        stripped = line.strip()
        if stripped.startswith("Limits:"):
            limits = {m.group(1): (int(m.group(2)), int(m.group(3))) for m in _LIMITS.finditer(stripped)}
        elif ":" in stripped and not stripped.startswith(("Capabilities:", "Playback channels:", "Capture channels:")):
            for m in _READING.finditer(stripped):
                readings.setdefault(m.group(1), (int(m.group(2)), float(m.group(3)) if m.group(3) else None))
    finish()
    return volumes


def read_volumes(card: int, run: Run = run_amixer) -> list[Volume]:
    """The card's volumes; OSError if amixer can't read them."""
    try:
        done = run(["amixer", "-c", str(card), "scontents"])
    except FileNotFoundError:
        raise OSError("amixer isn't installed (sudo apt install alsa-utils)") from None
    except subprocess.TimeoutExpired:
        raise OSError("amixer didn't respond") from None
    if done.returncode != 0:
        raise OSError((done.stderr.strip().splitlines() or ["amixer failed"])[-1])
    return parse_volumes(done.stdout)


def io_side(side: Side) -> Literal["input", "output"]:
    """Capture vs playback half of a Side."""
    return "input" if side in ("input", "link_input") else "output"


def side_volumes(side: Side, volumes: list[Volume]) -> list[Volume]:
    """What each side's sliders adjust: capture on the input card; playback
    on the output card, leaving out a mic's monitor path (a control that
    also captures), which only loops receive audio back out."""
    if io_side(side) == "input":
        return [v for v in volumes if v.direction == "capture"]
    capturing = {v.control for v in volumes if v.direction == "capture"}
    return [v for v in volumes if v.direction == "playback" and v.control not in capturing]


def set_volume(card: int, control: str, direction: str, value: int, run: Run = run_amixer) -> Optional[str]:
    """Set one volume; an error message, or None."""
    try:
        done = run(["amixer", "-q", "-c", str(card), "sset", control, direction, str(value)])
    except FileNotFoundError:
        return "amixer isn't installed (sudo apt install alsa-utils)"
    except subprocess.TimeoutExpired:
        return "amixer didn't respond"
    return None if done.returncode == 0 else (done.stderr.strip().splitlines() or ["amixer failed"])[-1]


def side_card(config, side: Side) -> Optional[int]:
    return alsa_card(getattr(config, _SIDE_DEVICE[side]))


def side_levels(config, side: Side) -> dict:
    return getattr(config, _SIDE_LEVELS[side])


def side_levels_field(side: Side) -> str:
    return _SIDE_LEVELS[side]


def side_label(side: Side) -> str:
    return _SIDE_LABEL[side]


def apply_saved_levels(config, run: Run = run_amixer, *, sides: Optional[tuple[Side, ...]] = None) -> None:
    """Set the saved levels on the chosen radios' cards, before an engine opens them."""
    for side in sides or ("input", "output", "link_input", "link_output"):
        levels = side_levels(config, side)
        if not levels:
            continue
        card = side_card(config, side)
        if card is None:
            continue
        direction = "capture" if io_side(side) == "input" else "playback"
        for control, value in levels.items():
            error = set_volume(card, control, direction, int(value), run)
            if error:
                _logger.warning("couldn't set %s on sound card %d: %s", control, card, error)


def levels_on_card(card: int, controls: set[str], run: Run = run_amixer) -> tuple[dict[str, int], dict[str, int]]:
    """The current input and output levels of `controls`, to save after a
    board preset sets them by name."""
    volumes = read_volumes(card, run)
    inputs = {v.control: v.value for v in side_volumes("input", volumes) if v.control in controls}
    outputs = {v.control: v.value for v in side_volumes("output", volumes) if v.control in controls}
    return inputs, outputs
