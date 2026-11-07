"""Prometheus text exposition for tower / multi-site scrapers.

``GET /metrics`` is intentionally unauthenticated (Prometheus convention).
Bind it behind a firewall or reverse-proxy ACL on a public host.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Optional, Protocol

from .activity import summarize


class _LiveAudioStatus(Protocol):
    def status(self) -> dict: ...


class _ActivityStore(Protocol):
    def rows(self, since: float, until: float) -> list: ...


def _escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _gauge(name: str, help_text: str, value: float, labels: Optional[dict[str, str]] = None) -> list[str]:
    return _metric(name, "gauge", help_text, value, labels)


def _counter(name: str, help_text: str, value: float, labels: Optional[dict[str, str]] = None) -> list[str]:
    return _metric(name, "counter", help_text, value, labels)


def _metric(
    name: str,
    kind: str,
    help_text: str,
    value: float,
    labels: Optional[dict[str, str]] = None,
) -> list[str]:
    label_text = ""
    if labels:
        parts = [f'{key}="{_escape_label(str(val))}"' for key, val in labels.items()]
        label_text = "{" + ",".join(parts) + "}"
    return [
        f"# HELP {name} {help_text}",
        f"# TYPE {name} {kind}",
        f"{name}{label_text} {value}",
    ]


def render_metrics(
    *,
    service,
    live_audio: Optional[_LiveAudioStatus] = None,
    activity_store: Optional[_ActivityStore] = None,
    process_started_at: Optional[float] = None,
    now: Optional[float] = None,
) -> str:
    """Build Prometheus text for the current repeater snapshot."""
    clock = now if now is not None else time.time()
    snap = service.snapshot()
    audio = live_audio.status() if live_audio is not None else {}
    lines: list[str] = []

    lines += _gauge(
        "moreopenrepeater_up",
        "1 when the API process is serving metrics",
        1,
    )
    if process_started_at is not None:
        lines += _gauge(
            "moreopenrepeater_process_start_time_seconds",
            "Unix time when this API process started",
            process_started_at,
        )
        lines += _gauge(
            "moreopenrepeater_process_uptime_seconds",
            "Seconds since this API process started",
            max(0.0, clock - process_started_at),
        )

    lines += _gauge(
        "moreopenrepeater_controller_state",
        "1 for the active RepeaterController state label",
        1,
        {"state": snap.state},
    )
    lines += _gauge("moreopenrepeater_ptt_active", "1 when PTT is asserted", 1 if snap.ptt_active else 0)
    lines += _gauge("moreopenrepeater_cos_active", "1 when COS is open", 1 if snap.cos_active else 0)
    lines += _gauge(
        "moreopenrepeater_transmitter_enabled",
        "1 when the transmitter is enabled (not dry-run / disabled)",
        1 if snap.transmitter_enabled else 0,
    )
    lines += _gauge("moreopenrepeater_locked_out", "1 when the controller is locked out", 1 if snap.locked_out else 0)
    lines += _gauge("moreopenrepeater_net_active", "1 when a net is running", 1 if snap.net_active else 0)
    lines += _gauge(
        "moreopenrepeater_linked_nodes",
        "Number of linked AllStar / network nodes reported in status",
        float(len(snap.linked_nodes)),
    )

    lines += _gauge(
        "moreopenrepeater_audio_engine_running",
        "1 when the live audio engine is running",
        1 if audio.get("running") else 0,
    )
    lines += _gauge(
        "moreopenrepeater_rx_level_dbfs",
        "Receive audio level in dBFS (−120 when the engine is stopped)",
        float(audio.get("rx_level_db", -120.0)),
    )
    for counter in (
        "dropped_input_blocks",
        "starved_output_blocks",
        "input_overflows",
        "output_underflows",
    ):
        lines += _counter(
            f"moreopenrepeater_audio_{counter}_total",
            f"Audio engine {counter.replace('_', ' ')} since the engine started",
            float(audio.get(counter, 0)),
        )

    filtered = getattr(getattr(service, "controller", None), "kerchunks_filtered", 0)
    lines += _counter(
        "moreopenrepeater_kerchunks_filtered_total",
        "Kerchunks dropped by the kerchunk filter since process start",
        float(filtered),
    )

    if activity_store is not None:
        since = clock - timedelta(hours=24).total_seconds()
        until_dt = datetime.fromtimestamp(clock)
        since_dt = datetime.fromtimestamp(since)
        summary = summarize(activity_store.rows(since, clock), since_dt, until_dt)
        for key, help_text in (
            ("rx_count", "Receive openings in the last 24 hours"),
            ("kerchunks", "Kerchunk-length RX openings in the last 24 hours"),
            ("timeouts", "Timed-out RX openings in the last 24 hours"),
            ("ids", "Station IDs played in the last 24 hours"),
            ("announcements", "Announcements played in the last 24 hours"),
            ("lockouts", "Lockouts in the last 24 hours"),
        ):
            lines += _counter(
                f"moreopenrepeater_activity_{key}",
                help_text,
                float(summary.get(key, 0)),
            )
        lines += _gauge(
            "moreopenrepeater_activity_rx_seconds",
            "Total RX airtime seconds in the last 24 hours",
            float(summary.get("rx_seconds", 0.0)),
        )
        lines += _gauge(
            "moreopenrepeater_activity_tx_seconds",
            "Total TX airtime seconds in the last 24 hours",
            float(summary.get("tx_seconds", 0.0)),
        )

    return "\n".join(lines) + "\n"
