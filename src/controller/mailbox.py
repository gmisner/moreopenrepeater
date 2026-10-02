"""Voice mailbox DTMF: digits in, "leave / play / delete" out.

    *7 12 #          leave a message in mailbox 12 (the next transmission is recorded)
    *8 12 * 1234 #   play mailbox 12's messages, with its PIN
    *9 12 * 1234 #   delete them

The codes are settings; unkeying after the digits works like `#`. What a
mailbox is, and whether the PIN is right, is up to api.mailbox.
"""
from __future__ import annotations

from typing import Optional

from .events import MailboxCommand

MAX_ENTRY_DIGITS = 16  # box + "*" + PIN


def parse_entry(action: str, entry: str) -> Optional[MailboxCommand]:
    if action == "leave":
        return MailboxCommand(action, entry) if entry.isdigit() and len(entry) <= 6 else None
    box, _, pin = entry.partition("*")
    if box.isdigit() and len(box) <= 6 and pin.isdigit():
        return MailboxCommand(action, box, pin)
    return None


class MailboxDialer:
    def __init__(self, interdigit_timeout: float = 5.0) -> None:
        self._interdigit_timeout = interdigit_timeout
        self._digits = ""
        self._last_digit_at: Optional[float] = None

    def handle_digit(self, digit: str, now: float, codes: dict[str, str]) -> Optional[MailboxCommand]:
        """`codes` maps "leave" / "play" / "delete" to their access codes."""
        self.tick(now)
        self._last_digit_at = now
        if digit == "#":
            return self._finish(codes)
        self._digits += digit
        live = [code for code in codes.values() if code]
        if not any(code.startswith(self._digits) or self._digits.startswith(code) for code in live):
            self._digits = digit if any(code.startswith(digit) for code in live) else ""
        if len(self._digits) > MAX_ENTRY_DIGITS + max((len(c) for c in live), default=0):
            self.reset()
        return None

    def carrier_dropped(self, codes: dict[str, str]) -> Optional[MailboxCommand]:
        return self._finish(codes)

    def tick(self, now: float) -> None:
        if self._last_digit_at is not None and now - self._last_digit_at > self._interdigit_timeout:
            self.reset()

    def reset(self) -> None:
        self._digits = ""
        self._last_digit_at = None

    def _finish(self, codes: dict[str, str]) -> Optional[MailboxCommand]:
        digits = self._digits
        self.reset()
        # The longest code that prefixes the digits, so "*7" and "*71" can coexist.
        matches = sorted(((code, action) for action, code in codes.items() if code and digits.startswith(code)), reverse=True)
        for code, action in matches:
            entry = digits[len(code):]
            if entry:
                return parse_entry(action, entry)
        return None
