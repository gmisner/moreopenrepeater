"""Minimal INI section/key helpers that keep comments and unknown keys."""
from __future__ import annotations

import re
from typing import Optional

_SECTION = re.compile(r"^\[([^\]]+)\]\s*(?:;.*)?$")
_KEY = re.compile(r"^([^;=\s][^=]*?)\s*=\s*(.*?)(\s+;.*)?$")


def section_values(text: str, section: str) -> dict[str, str]:
    """Key/value pairs under `[section]` (last write wins). Comments stripped."""
    values: dict[str, str] = {}
    current: Optional[str] = None
    for raw in text.splitlines():
        line = raw.strip()
        match = _SECTION.match(line)
        if match:
            current = match.group(1)
            continue
        if current != section or not line or line.startswith(";") or line.startswith("#"):
            continue
        key_match = _KEY.match(line)
        if key_match:
            values[key_match.group(1).strip()] = key_match.group(2).strip()
    return values


def set_values(text: str, section: str, updates: dict[str, str]) -> str:
    """Set keys under `[section]`, inserting the section at the end if missing."""
    if not updates:
        return text
    lines = text.splitlines()
    pending = dict(updates)
    out: list[str] = []
    current: Optional[str] = None
    section_seen = False
    i = 0
    while i < len(lines):
        raw = lines[i]
        match = _SECTION.match(raw.strip())
        if match:
            if current == section and pending:
                for key, value in pending.items():
                    out.append(f"{key} = {value}")
                pending.clear()
            current = match.group(1)
            if current == section:
                section_seen = True
            out.append(raw)
            i += 1
            continue
        if current == section:
            key_match = _KEY.match(raw.strip())
            if key_match:
                key = key_match.group(1).strip()
                if key in pending:
                    comment = key_match.group(3) or ""
                    out.append(f"{key} = {pending.pop(key)}{comment}")
                    i += 1
                    continue
        out.append(raw)
        i += 1
    if current == section and pending:
        for key, value in pending.items():
            out.append(f"{key} = {value}")
        pending.clear()
    if not section_seen:
        if out and out[-1].strip():
            out.append("")
        out.append(f"[{section}]")
        for key, value in updates.items():
            out.append(f"{key} = {value}")
    return "\n".join(out) + ("\n" if text.endswith("\n") or not text else "")
