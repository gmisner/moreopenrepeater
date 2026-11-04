"""Spoken wording for site-sensor readings ("Battery thirteen point two volts")."""
from __future__ import annotations

from typing import Optional

_ONES = (
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
    "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
    "seventeen", "eighteen", "nineteen",
)
_TENS = ("", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety")

_UNIT_WORDS = {
    "V": "volts",
    "v": "volts",
    "C": "degrees Celsius",
    "°C": "degrees Celsius",
    "F": "degrees Fahrenheit",
    "°F": "degrees Fahrenheit",
    "%": "percent",
    "psi": "P S I",
    "gal": "gallons",
    "L": "liters",
}


def speak_value(value: float, places: int = 1) -> str:
    """Whole numbers as words; one decimal as 'thirteen point two'."""
    negative = value < 0
    value = abs(value)
    rounded = round(value, places)
    whole = int(rounded)
    frac = int(round((rounded - whole) * (10**places)))
    if places > 0 and frac >= 10**places:
        whole += 1
        frac = 0
    words = ("minus " if negative else "") + _whole(whole)
    if places > 0 and frac:
        digits = f"{frac:0{places}d}".rstrip("0") or "0"
        words += " point " + " ".join(_ONES[int(d)] for d in digits)
    return words


def _whole(n: int) -> str:
    if n < 20:
        return _ONES[n]
    if n < 100:
        tens, ones = divmod(n, 10)
        return _TENS[tens] if ones == 0 else f"{_TENS[tens]} {_ONES[ones]}"
    if n < 1000:
        hundreds, rest = divmod(n, 100)
        if rest == 0:
            return f"{_ONES[hundreds]} hundred"
        return f"{_ONES[hundreds]} hundred {_whole(rest)}"
    # Large values: digit-by-digit so TTS stays clear.
    return " ".join(_ONES[int(d)] for d in str(n))


def unit_words(units: str) -> str:
    text = units.strip()
    return _UNIT_WORDS.get(text, text)


def speak_reading(name: str, value: Optional[float], units: str, places: int = 1) -> str:
    label = name.strip() or "Sensor"
    if value is None:
        return f"{label} is not available."
    return f"{label} {speak_value(value, places)} {unit_words(units)}".rstrip()
