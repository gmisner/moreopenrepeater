"""Remote base tuning: what users key by DTMF, and what the radio is told.

A remote base is the link radio (controller.link_radio) used on another
band: users switch it on, tune it by DTMF, and talk through it. These pure
helpers parse what's keyed after a tuning macro and check it against the
bands it may transmit on; api.remote_base sends it to the radio.

Frequencies are keyed in MHz with `*` as the decimal point (`146*52`, `52*525`)
or as plain digits where the first three are MHz (`14652` is 146.52 MHz), then
`#`. Tones are keyed in Hz the same way (`100*0`), or as digits with the last
one tenths (`1000` is 100.0 Hz, `885` is 88.5); `0` turns the tone off.
"""
from __future__ import annotations

import re
from typing import Optional

CTCSS_TONES = (
    67.0, 69.3, 71.9, 74.4, 77.0, 79.7, 82.5, 85.4, 88.5, 91.5, 94.8, 97.4, 100.0, 103.5, 107.2, 110.9, 114.8,
    118.8, 123.0, 127.3, 131.8, 136.5, 141.3, 146.2, 150.0, 151.4, 156.7, 159.8, 162.2, 165.5, 167.9, 171.3,
    173.8, 177.3, 179.9, 183.5, 186.2, 189.9, 192.8, 196.6, 199.5, 203.5, 206.5, 210.7, 218.1, 225.7, 229.1,
    233.6, 241.8, 250.3, 254.1,
)
# Usual repeater offsets by band (MHz), for a shift without its own offset.
STANDARD_OFFSETS = ((28.0, 29.7, 0.1), (50.0, 54.0, 1.0), (144.0, 148.0, 0.6), (222.0, 225.0, 1.6), (420.0, 450.0, 5.0), (902.0, 928.0, 12.0))
DEFAULT_RANGES = "144-148, 222-225, 420-450"
_RANGE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*$")
SHIFT_WORDS = {"simplex": "simplex", "plus": "plus offset", "minus": "minus offset"}


def parse_ranges(text: str) -> list[tuple[float, float]]:
    """'144-148, 420-450' as [(144.0, 148.0), (420.0, 450.0)] (MHz). Raises ValueError."""
    ranges = []
    for part in filter(str.strip, text.split(",")):
        match = _RANGE.match(part)
        if not match:
            raise ValueError(f"{part.strip()!r} isn't a range like 144-148")
        low, high = float(match[1]), float(match[2])
        if not 0 < low < high <= 10000:
            raise ValueError(f"{part.strip()!r} isn't a range of MHz, low to high")
        ranges.append((low, high))
    return ranges


def in_ranges(mhz: float, ranges: list[tuple[float, float]]) -> bool:
    return any(low <= mhz <= high for low, high in ranges)


def parse_frequency(entry: str) -> Optional[float]:
    """MHz from a keyed entry, or None if it isn't one."""
    if "*" in entry:
        whole, _, fraction = entry.partition("*")
        if not whole.isdigit() or (fraction and not fraction.isdigit()) or "*" in fraction:
            return None
        return float(f"{whole}.{fraction or 0}")
    if not entry.isdigit() or len(entry) < 3:
        return None
    return float(f"{entry[:3]}.{entry[3:] or 0}")


def parse_tone(entry: str) -> Optional[float]:
    """A CTCSS tone in Hz, 0.0 for off, or None if it isn't a standard tone."""
    if entry in ("0", "00"):
        return 0.0
    if "*" in entry:
        whole, _, fraction = entry.partition("*")
        if not whole.isdigit() or not (fraction == "" or fraction.isdigit()):
            return None
        hz = float(f"{whole}.{fraction or 0}")
    elif entry.isdigit() and len(entry) >= 3:
        hz = int(entry) / 10
    else:
        return None
    return next((tone for tone in CTCSS_TONES if abs(tone - hz) < 0.05), None)


def standard_offset(mhz: float) -> Optional[float]:
    return next((offset for low, high, offset in STANDARD_OFFSETS if low <= mhz <= high), None)


def offset_mhz(mhz: float, shift: str, offset: Optional[float]) -> float:
    """How far the transmitter is moved for `shift`, in MHz (0 for simplex)."""
    if shift == "simplex":
        return 0.0
    return offset if offset is not None else (standard_offset(mhz) or 0.0)


def transmit_mhz(mhz: float, shift: str, offset: Optional[float]) -> float:
    sign = {"plus": 1, "minus": -1}.get(shift, 0)
    return mhz + sign * offset_mhz(mhz, shift, offset)


def spoken_mhz(mhz: float) -> str:
    text = f"{mhz:.4f}".rstrip("0")
    return text + "0" if text.endswith(".") else text


def summary(mhz: float, shift: str, tone: Optional[float]) -> str:
    """'146.94, minus offset, tone 100.0'."""
    parts = [spoken_mhz(mhz), SHIFT_WORDS[shift]]
    parts.append(f"tone {tone:.1f}" if tone else "no tone")
    return ", ".join(parts)
