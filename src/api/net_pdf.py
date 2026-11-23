"""Minimal single-net PDF export (stdlib only — no reportlab on the Pi)."""
from __future__ import annotations

from datetime import datetime
from typing import Any


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _line(parts: list[str], widths: list[int]) -> str:
    cells = []
    for part, width in zip(parts, widths):
        cell = (part or "")[:width]
        cells.append(cell.ljust(width)[:width])
    return "  ".join(cells).rstrip()


def net_pdf(net: dict[str, Any]) -> bytes:
    """A plain-text PDF of one net's check-ins and desk metadata."""
    started = datetime.fromtimestamp(net["started_at"]).strftime("%Y-%m-%d %H:%M")
    ended = ""
    if net.get("ended_at"):
        ended = datetime.fromtimestamp(net["ended_at"]).strftime("%Y-%m-%d %H:%M")
    control = " ".join(p for p in (net.get("control_callsign", ""), net.get("control_name", "")) if p)
    logger = " ".join(p for p in (net.get("logger_callsign", ""), net.get("logger_name", "")) if p)
    lines = [
        f"Net: {net.get('name', '')}",
        f"Club: {net.get('club_name', '')}    Frequency: {net.get('frequency', '')}",
        f"Net control: {control}    Logger: {logger}",
        f"Started: {started}" + (f"    Ended: {ended}" if ended else ""),
        f"Check-ins: {len(net.get('checkins', []))}",
        "",
        _line(["#", "Time", "Callsign", "Name", "Location", "Class", "Comments", "Notes"], [3, 8, 10, 14, 16, 8, 8, 24]),
        "-" * 100,
    ]
    for index, checkin in enumerate(net.get("checkins", []), start=1):
        at = datetime.fromtimestamp(checkin["at"]).strftime("%H:%M:%S")
        lines.append(
            _line(
                [
                    str(index),
                    at,
                    checkin.get("callsign", ""),
                    checkin.get("name", ""),
                    checkin.get("location", ""),
                    checkin.get("license_class", ""),
                    "yes" if checkin.get("staying_for_comments") else "no",
                    checkin.get("notes", ""),
                ],
                [3, 8, 10, 14, 16, 8, 8, 24],
            )
        )
    if not net.get("checkins"):
        lines.append("(none)")

    content_lines = ["BT", "/F1 9 Tf", "40 750 Td", "11 TL"]
    for i, line in enumerate(lines):
        content_lines.append(f"({_escape(line)}) Tj")
        if i < len(lines) - 1:
            content_lines.append("T*")
    content_lines.append("ET")
    stream = "\n".join(content_lines).encode("latin-1", errors="replace")

    objects: list[bytes] = [
        b"1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj\n",
        b"2 0 obj<< /Type /Pages /Kids [3 0 R] /Count 1 >>endobj\n",
        (
            b"3 0 obj<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>endobj\n"
        ),
        b"4 0 obj<< /Length %d >>stream\n" % len(stream) + stream + b"\nendstream\nendobj\n",
        b"5 0 obj<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>endobj\n",
    ]

    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objects:
        offsets.append(len(out))
        out.extend(obj)
    xref = len(out)
    out.extend(f"xref\n0 {len(offsets)}\n".encode())
    out.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        out.extend(f"{offset:010d} 00000 n \n".encode())
    out.extend(f"trailer<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return bytes(out)
