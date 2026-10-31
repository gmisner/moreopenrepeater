"""Support bundles: a zip of recent logs and safe diagnostics for
troubleshooting radio configuration, audio and related setup. No passwords,
env file, users, tokens or recordings.
"""
from __future__ import annotations

import collections
import json
import re
import subprocess
import time
import zipfile
from pathlib import Path
from typing import Callable, Optional

from .service import RepeaterService
from .updates import Updater, installed_version, read_log

LOG_LINES = 5000
JOURNAL_LINES = 500
UPDATE_LOG_LINES = 500


def support_bundle_name(callsign: str, now: float) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", callsign.lower()).strip("-") or "repeater"
    return f"moreopenrepeater-support-{slug}-{time.strftime('%Y%m%d-%H%M%S', time.localtime(now))}.zip"


def _tail_lines(path: Path, lines: int) -> list[str]:
    if not path.exists():
        return []
    with path.open(errors="replace") as handle:
        return [line.rstrip("\n") for line in collections.deque(handle, maxlen=lines)]


def _journal(run: Callable[..., subprocess.CompletedProcess], lines: int) -> str:
    try:
        result = run(
            ["journalctl", "-u", "moreopenrepeater", "-n", str(lines), "--no-pager"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return f"(journalctl unavailable: {error})\n"
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip() or f"exit {result.returncode}"
        return f"(journalctl failed: {err})\n"
    return result.stdout or "(empty)\n"


def write_support_bundle(
    path: Path,
    *,
    service: RepeaterService,
    log_path: Path,
    repo_dir: Path,
    update_channel: str,
    live_audio_status: Optional[dict],
    system_readings: Optional[dict],
    updater: Optional[Updater],
    now: float,
    receiver_openings: Optional[list] = None,
    audio_glitches: Optional[list] = None,
    last_unexpected_stop: Optional[dict] = None,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> None:
    """Writes a support-bundle archive to `path`."""
    callsign = service.config.callsign or "repeater"
    version = installed_version(repo_dir)
    settings = service.export_snapshot()
    status = vars(service.snapshot())
    update_lines = read_log(updater.settings, UPDATE_LOG_LINES) if updater and updater.settings else []
    health = {
        "system": system_readings if system_readings is not None else {},
        "last_unexpected_stop": last_unexpected_stop,
    }
    readme = (
        "moreopenrepeater support bundle\n"
        "================================\n"
        f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S %z', time.localtime(now))}\n"
        f"Callsign:  {callsign}\n"
        f"Channel:   {update_channel}\n"
        "\n"
        "Contents for radio/audio troubleshooting:\n"
        "  config.json         settings, macros, announcements\n"
        "  status.json         controller state at capture\n"
        "  audio-engine.json   levels, COS, TX, device names\n"
        "  openings.json       recent squelch openings (levels, DTMF, after_tx)\n"
        "  glitches.json       audio underruns/overruns\n"
        "  health.json         Pi readings + last unexpected stop\n"
        "  moreopenrepeater.log / journal.txt / update.log\n"
        "\n"
        "It does not include passwords, the env file, users, tokens or recordings.\n"
        "Share the whole .zip with whoever is helping you.\n"
    )
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("README.txt", readme)
        archive.writestr(
            "version.json",
            json.dumps({"channel": update_channel, "installed": version}, indent=2) + "\n",
        )
        archive.writestr("config.json", json.dumps(settings, indent=2, default=str) + "\n")
        archive.writestr("status.json", json.dumps(status, indent=2, default=str) + "\n")
        archive.writestr(
            "audio-engine.json",
            json.dumps(live_audio_status if live_audio_status is not None else {}, indent=2, default=str) + "\n",
        )
        archive.writestr(
            "openings.json",
            json.dumps(list(receiver_openings or []), indent=2, default=str) + "\n",
        )
        archive.writestr(
            "glitches.json",
            json.dumps(list(audio_glitches or []), indent=2, default=str) + "\n",
        )
        archive.writestr("health.json", json.dumps(health, indent=2, default=str) + "\n")
        archive.writestr(
            "system.json",
            json.dumps(system_readings if system_readings is not None else {}, indent=2, default=str) + "\n",
        )
        archive.writestr("moreopenrepeater.log", "\n".join(_tail_lines(log_path, LOG_LINES)) + "\n")
        archive.writestr("journal.txt", _journal(run, JOURNAL_LINES))
        archive.writestr("update.log", "\n".join(update_lines) + ("\n" if update_lines else ""))
