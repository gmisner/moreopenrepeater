"""Autopatch dialing: DTMF digits in, "dial this number" / "hang up" out.

A user sends the access code and the phone number, then `#` or simply
unkeys:  *6 5551234567 #  dials 5551234567. While a call is up (ringing or
connected), the hangup code on its own ends it.

Which numbers may be dialed is a list of patterns in Asterisk's dialplan
notation: X is any digit, N is 2-9, Z is 1-9, and digits match themselves.
"911 NXXNXXXXXX" allows 911 and ten-digit North American numbers, which
rules out 1+ long distance and short service codes like 411.
"""
from __future__ import annotations

import re
from typing import Optional

from .events import ControllerCommand, DialPatch, HangupPatch

MAX_NUMBER_DIGITS = 15  # E.164's limit
_PATTERN = re.compile(r"[0-9XNZ]+")
_CLASSES = {"X": "0123456789", "N": "23456789", "Z": "123456789"}


def parse_patterns(text: str) -> list[str]:
    return [p for p in re.split(r"[\s,]+", text.upper()) if p]


def valid_patterns(text: str) -> bool:
    return all(_PATTERN.fullmatch(p) for p in parse_patterns(text))


def matches(number: str, pattern: str) -> bool:
    return len(number) == len(pattern) and all(
        digit in _CLASSES.get(p, p) for digit, p in zip(number, pattern)
    )


def number_allowed(number: str, allowed: str, blocked: str) -> bool:
    return (
        number.isdigit()
        and any(matches(number, p) for p in parse_patterns(allowed))
        and not any(matches(number, p) for p in parse_patterns(blocked))
    )


class AutopatchDialer:
    def __init__(self, interdigit_timeout: float = 5.0) -> None:
        self._interdigit_timeout = interdigit_timeout
        self._digits = ""
        self._last_digit_at: Optional[float] = None
        self.call_active = False  # set by whoever places calls; enables the hangup code

    def handle_digit(self, digit: str, now: float, access_code: str, hangup_code: str) -> Optional[ControllerCommand]:
        self.tick(now)
        self._last_digit_at = now
        self._digits += digit
        if self.call_active:
            return self._match_code(hangup_code, digit, HangupPatch())
        if len(self._digits) <= len(access_code):
            self._match_code(access_code, digit, None)
            return None
        if digit == "#":
            return self._dial(access_code, self._digits[:-1])
        if not digit.isdigit() or len(self._digits) - len(access_code) > MAX_NUMBER_DIGITS:
            self.reset()
        return None

    def carrier_dropped(self, access_code: str) -> Optional[ControllerCommand]:
        """Unkeying after the number dials it, like pressing `#`."""
        if self.call_active or len(self._digits) <= len(access_code):
            self.reset()
            return None
        return self._dial(access_code, self._digits)

    def tick(self, now: float) -> None:
        if self._last_digit_at is not None and now - self._last_digit_at > self._interdigit_timeout:
            self.reset()

    def reset(self) -> None:
        self._digits = ""
        self._last_digit_at = None

    def _match_code(self, code: str, digit: str, on_match: Optional[ControllerCommand]) -> Optional[ControllerCommand]:
        """Keep `_digits` a prefix of `code`, restarting from `digit` on a mismatch."""
        if not code.startswith(self._digits):
            self._digits = digit if code.startswith(digit) else ""
        if on_match is not None and self._digits == code:
            self.reset()
            return on_match
        return None

    def _dial(self, access_code: str, digits: str) -> Optional[ControllerCommand]:
        self.reset()
        number = digits[len(access_code):]
        return DialPatch(number=number) if number.isdigit() else None
