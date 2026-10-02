"""Streams what's on the air to an Icecast server, such as a Broadcastify feed.

ffmpeg does the MP3 encoding and the Icecast connection; this feeds it the
transmitted audio from the live-audio monitor, with silence when there's
none (Icecast servers drop a source that stops sending). If ffmpeg exits (a
wrong password, the server down), it's restarted with a growing delay.

The settings, password included, live in their own file
(`data/stream.json`), out of the settings API and backups.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import time
from typing import Callable, Optional
from urllib.parse import quote

from .monitor import AudioMonitor
from .persistence import StateStore

_logger = logging.getLogger("moreopenrepeater.stream")

DEFAULT_SETTINGS = {
    "enabled": False,
    "host": "",
    "port": 80,
    "mount": "",
    "username": "source",
    "password": "",
    "name": "",
    "description": "",
    "genre": "Amateur Radio",
    "bitrate": 16,  # Broadcastify wants 16 kbps mono MP3 at 22.05 kHz
    "legacy_icecast": False,
}
SECRET_FIELDS = ("password",)
SILENCE_SECONDS = 0.5
STREAMING_AFTER_SECONDS = 5.0  # ffmpeg gives up within a second or two on a bad password or address
RETRY_SECONDS = (5, 15, 30, 60, 120, 300)
NO_FFMPEG_RECHECK_SECONDS = 60.0


def ffmpeg_command(ffmpeg: str, settings: dict, sample_rate: int) -> list[str]:
    mount = "/" + settings["mount"].lstrip("/")
    url = f"icecast://{quote(settings['username'], safe='')}@{settings['host']}:{settings['port']}{quote(mount)}"
    command = [
        ffmpeg, "-hide_banner", "-nostats", "-loglevel", "error",
        "-f", "s16le", "-ar", str(sample_rate), "-ac", "1", "-i", "pipe:0",
        "-ac", "1", "-ar", "22050", "-c:a", "libmp3lame", "-b:a", f"{settings['bitrate']}k",
        "-f", "mp3", "-content_type", "audio/mpeg", "-password", settings["password"],
    ]
    for option in ("name", "description", "genre"):
        if settings[option]:
            command += [f"-ice_{option}", settings[option]]
    if settings["legacy_icecast"]:
        command += ["-legacy_icecast", "1"]
    return command + [url]


def complete(settings: dict) -> bool:
    return bool(settings["host"] and settings["mount"] and settings["password"])


class Streamer:
    def __init__(
        self,
        monitor: AudioMonitor,
        store: Optional[StateStore],
        sample_rate: Callable[[], int],
        which: Callable[[str], Optional[str]] = shutil.which,
        spawn=asyncio.create_subprocess_exec,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._monitor = monitor
        self._store = store
        self._sample_rate = sample_rate
        self._which = which
        self._spawn = spawn
        self._clock = clock
        saved = (store.load() if store is not None else None) or {}
        self._settings = {**DEFAULT_SETTINGS, **{k: v for k, v in saved.items() if k in DEFAULT_SETTINGS}}
        self._changed: Optional[asyncio.Event] = None
        self.status = {"state": "off", "detail": "", "since": clock()}

    # -- settings -----------------------------------------------------------

    def public_settings(self) -> dict:
        shown = {k: v for k, v in self._settings.items() if k not in SECRET_FIELDS}
        return {**shown, "password_set": bool(self._settings["password"])}

    def update(self, changes: dict) -> dict:
        """`password` None or "" keeps the saved one."""
        changes = {k: v for k, v in changes.items() if k in DEFAULT_SETTINGS and v is not None}
        if not changes.get("password"):
            changes.pop("password", None)
        self._settings = {**self._settings, **changes}
        if self._store is not None:
            self._store.save(self._settings)
        if self._changed is not None:
            self._changed.set()
        return self.public_settings()

    # -- running ------------------------------------------------------------

    def _set_status(self, state: str, detail: str = "") -> None:
        if (state, detail) != (self.status["state"], self.status["detail"]):
            self.status = {"state": state, "detail": detail, "since": self._clock()}

    async def _wait_for_change(self, timeout: Optional[float] = None) -> None:
        assert self._changed is not None
        try:
            await asyncio.wait_for(self._changed.wait(), timeout)
        except asyncio.TimeoutError:
            pass
        self._changed.clear()

    async def run(self) -> None:
        """Runs until cancelled."""
        self._changed = asyncio.Event()
        failures = 0
        while True:
            settings = dict(self._settings)
            if not settings["enabled"]:
                self._set_status("off")
                failures = 0
                await self._wait_for_change()
                continue
            if not complete(settings):
                self._set_status("error", "Needs the server, mount and password.")
                await self._wait_for_change()
                continue
            ffmpeg = self._which("ffmpeg")
            if ffmpeg is None:
                self._set_status("error", "ffmpeg isn't installed (sudo apt install ffmpeg).")
                await self._wait_for_change(NO_FFMPEG_RECHECK_SECONDS)
                continue
            self._set_status("connecting")
            streamed_for, error = await self._stream_once(ffmpeg, settings)
            if self._changed.is_set():
                self._changed.clear()
                failures = 0
                continue
            failures = 0 if streamed_for > 60 else failures + 1
            delay = RETRY_SECONDS[min(max(failures - 1, 0), len(RETRY_SECONDS) - 1)]
            detail = error or "ffmpeg stopped."
            _logger.warning("stream to %s stopped: %s; retrying in %d s", settings["host"], detail, delay)
            self._set_status("retrying", f"{detail} Trying again in {delay} s.")
            await self._wait_for_change(delay)

    async def _stream_once(self, ffmpeg: str, settings: dict) -> tuple[float, str]:
        """Streams until ffmpeg exits or the settings change. Returns how long
        it ran and ffmpeg's last error line."""
        assert self._changed is not None
        rate = self._sample_rate()
        silence = bytes(int(rate * SILENCE_SECONDS) * 2)
        started = self._clock()
        try:
            process = await self._spawn(
                *ffmpeg_command(ffmpeg, settings, rate),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as error:
            return 0.0, f"Couldn't start ffmpeg: {error}"
        queue = self._monitor.subscribe("tx")
        try:
            while process.returncode is None and not self._changed.is_set():
                try:
                    frame = await asyncio.wait_for(queue.get(), SILENCE_SECONDS)
                except asyncio.TimeoutError:
                    frame = silence
                try:
                    process.stdin.write(frame)
                    await process.stdin.drain()
                except (BrokenPipeError, ConnectionResetError):
                    break
                if self.status["state"] == "connecting" and self._clock() - started >= STREAMING_AFTER_SECONDS:
                    _logger.info("streaming to %s%s", settings["host"], "/" + settings["mount"].lstrip("/"))
                    self._set_status("streaming")
        finally:
            self._monitor.unsubscribe(queue)
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), 5)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
        output = (await process.stderr.read()).decode(errors="replace").strip()
        last_line = output.splitlines()[-1].strip() if output else ""
        if settings["password"]:
            last_line = last_line.replace(settings["password"], "***")
        return self._clock() - started, last_line[:200]
