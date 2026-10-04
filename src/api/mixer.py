"""Sound card levels: the ALSA mixer volumes of the repeater's interface,
saved with the rest of the config and set again whenever the audio engine
opens the card.

A USB interface forgets its levels when it's unplugged, and the service
can't run `alsactl store` (it isn't root), so the controller keeps them
itself: the receive side's capture volumes on the input device's card, and
the transmit side's playback volumes on the output device's card. They're
saved by control name, not card number, so they follow the interface if
ALSA numbers the cards differently after a reboot.
"""
from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass
from typing import Callable, Literal, Optional

from .boards import alsa_card, run_amixer

_logger = logging.getLogger("moreopenrepeater.mixer")

Side = Literal["input", "output"]
Run = Callable[[list[str]], subprocess.CompletedProcess]

_CONTROL = re.compile(r"^Simple mixer control '(.+)',(\d+)$")
_LIMITS = re.compile(r"(Playback|Capture) (-?\d+) - (-?\d+)")
_READING = re.compile(r"(Playback|Capture) (-?\d+) \[\d+%\](?: \[(-?[\d.]+)dB\])?")


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


def side_volumes(side: Side, volumes: list[Volume]) -> list[Volume]:
    """What each side's sliders adjust: capture on the input card; playback
    on the output card, leaving out a mic's monitor path (a control that
    also captures), which only loops receive audio back out."""
    if side == "input":
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
    return alsa_card(config.audio_input_device if side == "input" else config.audio_output_device)


def apply_saved_levels(config, run: Run = run_amixer) -> None:
    """Set the saved levels on the interface's cards, before the engine opens them."""
    for side, levels, direction in (
        ("input", config.audio_input_levels, "capture"),
        ("output", config.audio_output_levels, "playback"),
    ):
        if not levels:
            continue
        card = side_card(config, side)
        if card is None:
            continue
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
