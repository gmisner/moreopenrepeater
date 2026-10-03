"""What the "help" DTMF action says: each macro's digits and what it does,
then the phone patch and mailbox codes when they're on.

Macros hidden from help (`Macro.hidden_from_help`: by default the
transmitter and link radio switches and anything needing a one-time code)
and the help macros themselves aren't read. A help macro's argument is a
page number; the list is only split into pages of `PAGE_SIZE` when there's a
help macro for the next page to point to.
"""
from __future__ import annotations

from typing import Callable

from .macros import Macro
from .state_machine import RepeaterConfig

PAGE_SIZE = 8

_DIGIT_WORDS = {
    "0": "zero", "1": "one", "2": "two", "3": "three", "4": "four", "5": "five", "6": "six", "7": "seven",
    "8": "eight", "9": "nine", "*": "star", "#": "pound", "A": "A", "B": "B", "C": "C", "D": "D",
}

ACTION_WORDS = {
    "link": "a link command",
    "time": "the time",
    "weather": "weather alerts",
    "id": "station I D",
    "announcement": "an announcement",
    "say": "a message",
    "parrot": "parrot, which plays your next transmission back",
    "tx_disable": "transmitter off",
    "tx_enable": "transmitter on",
    "aprs": "A P R S stations nearby",
    "gpio": "switch an output",
    "lockout_clear": "clear a lockout",
    "net_start": "start a net",
    "net_end": "end the net",
    "homeassistant": "home automation",
    "link_radio_on": "link radio on",
    "link_radio_off": "link radio off",
    "help": "this help",
}


def spoken_digits(pattern: str) -> str:
    """'*81' as 'star eight one'."""
    return " ".join(_DIGIT_WORDS.get(char, char) for char in pattern.upper())


def _entries(macros: list[Macro], config: RepeaterConfig, held: Callable[[str], str]) -> list[str]:
    entries = []
    for macro in macros:
        if macro.action == "help" or macro.hidden_from_help:
            continue
        what = macro.description.strip().rstrip(".") or ACTION_WORDS.get(macro.action, macro.action)
        entries.append(f"{spoken_digits(macro.pattern)}, {what}.")
    if config.autopatch_enabled and not held("autopatch"):
        entries.append(f"{spoken_digits(config.autopatch_access_code)} and a phone number, phone patch.")
    if config.mailbox_enabled:
        entries.append(f"{spoken_digits(config.mailbox_leave_code)} and a mailbox number, leave a message.")
        entries.append(f"{spoken_digits(config.mailbox_play_code)}, play your messages.")
    return entries


def help_text(macros: list[Macro], config: RepeaterConfig, page_argument: str = "", held: Callable[[str], str] = lambda _: "") -> str:
    """`held(feature)` says why a switched-on feature is off right now (net or GMRS mode), else ""."""
    entries = _entries(macros, config, held)
    if not entries:
        return "There are no commands to list."
    page = int(page_argument) if page_argument.strip().isdigit() and int(page_argument) > 0 else 1
    start = (page - 1) * PAGE_SIZE
    if start >= len(entries):
        return "There are no more commands."
    next_help = next(
        (m for m in macros if m.action == "help" and m.command.strip() == str(page + 1) and not m.hidden_from_help), None
    )
    more = start + PAGE_SIZE < len(entries) and next_help is not None
    shown = entries[start:start + PAGE_SIZE] if more else entries[start:]
    intro = "Repeater commands." if page == 1 else f"Commands, page {page}."
    parts = [intro, *shown]
    if more:
        parts.append(f"For more, key {spoken_digits(next_help.pattern)}.")
    return " ".join(parts)
