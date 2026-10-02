"""Transcripts of recordings and mailbox messages, with the callsigns heard.

Two engines, both off until picked in the settings:

- `vosk`: offline, on the controller itself. Needs `pip install vosk` and a
  model unpacked in `data/vosk-model` (the 40 MB small English one keeps up on
  a Raspberry Pi 4).
- `openai`: any server with OpenAI's `/v1/audio/transcriptions` -- a
  whisper.cpp or faster-whisper server on the LAN, or OpenAI itself, with the
  key in `MOREOPENREPEATER_TRANSCRIPTION_API_KEY`.

A transcript is a `.txt` file beside the clip's `.wav`; an empty one means
nothing was understood, so it isn't tried again. A background pass picks up
whatever hasn't been transcribed yet, newest first, one clip at a time.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Protocol

import numpy as np

from playout.wav import read_wav

from .service import RepeaterService

_logger = logging.getLogger("moreopenrepeater.transcripts")

API_KEY_ENV = "MOREOPENREPEATER_TRANSCRIPTION_API_KEY"
VOSK_MODEL_ENV = "MOREOPENREPEATER_VOSK_MODEL"

PASS_LIMIT = 5  # clips per background pass, so a backlog doesn't hog a worker
RECENT_CLIPS = 50  # only look this far back for clips without a transcript
TIMEOUT_SECONDS = 120.0
RETRY_SECONDS = 5 * 60  # after an error, before trying again
MAX_ATTEMPTS = 3  # per clip, before giving up on it
VOSK_RATE = 16000

_PHONETIC = {
    "alpha": "A", "alfa": "A", "bravo": "B", "charlie": "C", "delta": "D", "echo": "E", "foxtrot": "F",
    "golf": "G", "hotel": "H", "india": "I", "juliet": "J", "juliett": "J", "kilo": "K", "lima": "L",
    "mike": "M", "november": "N", "oscar": "O", "papa": "P", "quebec": "Q", "romeo": "R", "sierra": "S",
    "tango": "T", "uniform": "U", "victor": "V", "whiskey": "W", "whisky": "W", "x-ray": "X", "xray": "X",
    "yankee": "Y", "zulu": "Z",
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7",
    "eight": "8", "nine": "9", "niner": "9",
}
_AMATEUR = re.compile(r"(?:[A-Z]{1,2}|[0-9][A-Z]|[A-Z][0-9])[0-9][A-Z]{1,4}")
_GMRS = re.compile(r"[A-Z]{4}[0-9]{3}")
_TOKEN = re.compile(r"[A-Za-z0-9]+(?:['\u2019][A-Za-z]+)?(?:-[A-Za-z]+)?")


def _is_callsign(text: str) -> bool:
    return bool(_AMATEUR.fullmatch(text) or _GMRS.fullmatch(text))


def callsigns(text: str) -> list[str]:
    """Callsigns in a transcript, whether written out ("W1AW"), spelled
    ("W 1 A W") or said phonetically ("whiskey one alpha whiskey")."""
    pieces: list[str] = []
    run = ""
    tokens = _TOKEN.findall(text)
    for index, token in enumerate(tokens):
        letter = _PHONETIC.get(token.lower())
        if letter is None and len(token) == 1:
            following = tokens[index + 1].lower() if index + 1 < len(tokens) else ""
            if not run and token.lower() in ("a", "i") and following in _PHONETIC:
                letter = None  # "this is a kilo one ...": the word, not a letter
            else:
                letter = token.upper()
        if letter is not None:
            run += letter
            continue
        if run:
            pieces.append(run)
            run = ""
        pieces.append(token.upper())
    if run:
        pieces.append(run)

    found: list[str] = []
    for piece in pieces:
        start = 0
        while start < len(piece):
            for end in range(min(len(piece), start + 7), start + 2, -1):
                if _is_callsign(piece[start:end]):
                    if piece[start:end] not in found:
                        found.append(piece[start:end])
                    start = end
                    break
            else:
                start += 1
    return found


class TranscriptionError(Exception):
    pass


class ClipSource(Protocol):
    """Somewhere clips live: the recordings, or the mailbox."""

    def recent_ids(self, limit: int) -> list[str]: ...
    def path_for(self, clip_id: str) -> Path: ...
    def transcript_path(self, clip_id: str) -> Path: ...


def read_transcript(source: ClipSource, clip_id: str) -> Optional[str]:
    try:
        return source.transcript_path(clip_id).read_text(encoding="utf-8")
    except (KeyError, OSError):
        return None


def write_transcript(source: ClipSource, clip_id: str, text: str) -> None:
    path = source.transcript_path(clip_id)
    tmp = path.with_suffix(".txt.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


# -- engines ----------------------------------------------------------------


def _multipart(fields: dict[str, str], filename: str, data: bytes) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        "Content-Type: audio/wav\r\n\r\n".encode()
    )
    parts += [data, f"\r\n--{boundary}--\r\n".encode()]
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _post(url: str, body: bytes, headers: dict[str, str], timeout: float) -> bytes:
    request = urllib.request.Request(url, data=body, headers={"User-Agent": "moreopenrepeater", **headers}, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def transcribe_openai(
    path: Path, url: str, model: str, api_key: Optional[str], prompt: str,
    post: Callable[[str, bytes, dict[str, str], float], bytes] = _post,
) -> str:
    if not url:
        raise TranscriptionError("no transcription server address is set")
    fields = {"model": model, "response_format": "json"}
    if prompt:
        fields["prompt"] = prompt
    body, content_type = _multipart(fields, path.name, path.read_bytes())
    headers = {"Content-Type": content_type}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        reply = post(url, body, headers, TIMEOUT_SECONDS)
    except urllib.error.HTTPError as error:
        detail = error.read(300).decode("utf-8", "replace").strip()
        raise TranscriptionError(f"the server said {error.code} {error.reason}: {detail}"[:300]) from error
    except (urllib.error.URLError, OSError) as error:
        raise TranscriptionError(f"couldn't reach the transcription server: {getattr(error, 'reason', error)}") from error
    try:
        return str(json.loads(reply)["text"]).strip()
    except (ValueError, KeyError, TypeError) as error:
        raise TranscriptionError("the transcription server's reply had no text") from error


class VoskEngine:
    """Loads the model once, on first use (it takes a few seconds)."""

    def __init__(self, model_dir: Path) -> None:
        self.model_dir = model_dir
        self._model = None
        self._lock = threading.Lock()

    @staticmethod
    def installed() -> bool:
        try:
            import vosk  # noqa: F401
        except ImportError:
            return False
        return True

    def model_present(self) -> bool:
        return (self.model_dir / "am").is_dir() or (self.model_dir / "conf").is_dir()

    def transcribe(self, path: Path) -> str:
        try:
            import vosk
        except ImportError as error:
            raise TranscriptionError("Vosk isn't installed (pip install vosk)") from error
        if not self.model_present():
            raise TranscriptionError(f"no Vosk model in {self.model_dir}")
        with self._lock:
            if self._model is None:
                vosk.SetLogLevel(-1)
                self._model = vosk.Model(str(self.model_dir))
        samples = read_wav(path, VOSK_RATE)
        pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2").tobytes()
        recognizer = vosk.KaldiRecognizer(self._model, VOSK_RATE)
        texts = []
        for start in range(0, len(pcm), 8000):
            if recognizer.AcceptWaveform(pcm[start : start + 8000]):
                texts.append(json.loads(recognizer.Result()).get("text", ""))
        texts.append(json.loads(recognizer.FinalResult()).get("text", ""))
        return " ".join(t for t in texts if t).strip()


# -- the background pass ----------------------------------------------------


@dataclass
class TranscriberStatus:
    last_error: Optional[str] = None
    transcribed: int = 0


class Transcriber:
    def __init__(
        self,
        service: RepeaterService,
        sources: list[ClipSource],
        vosk: VoskEngine,
        api_key: Optional[str],
        post: Callable[[str, bytes, dict[str, str], float], bytes] = _post,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._service = service
        self._sources = sources
        self.vosk = vosk
        self.api_key = api_key
        self._post = post
        self._clock = clock
        self.status = TranscriberStatus()
        self._attempts: dict[str, int] = {}
        self._paused_until = 0.0
        self._settings: tuple = ()
        self._lock = threading.Lock()

    def _engine_settings(self) -> tuple:
        config = self._service.config
        return (config.transcription_engine, config.transcription_url, config.transcription_model)

    def transcribe(self, path: Path) -> str:
        config = self._service.config
        if config.transcription_engine == "vosk":
            return self.vosk.transcribe(path)
        if config.transcription_engine == "openai":
            prompt = f"Amateur radio repeater {config.callsign}. Callsigns are spoken phonetically." if config.callsign else ""
            return transcribe_openai(path, config.transcription_url, config.transcription_model, self.api_key, prompt, self._post)
        raise TranscriptionError("transcription is off")

    def pending(self) -> list[tuple[ClipSource, str]]:
        found = []
        for source in self._sources:
            for clip_id in source.recent_ids(RECENT_CLIPS):
                if self._attempts.get(clip_id, 0) < MAX_ATTEMPTS and read_transcript(source, clip_id) is None:
                    found.append((source, clip_id))
        return found

    def run_pending(self, limit: int = PASS_LIMIT) -> int:
        """Blocking: run it off the event loop. Returns how many it transcribed."""
        if self._service.config.transcription_engine == "off":
            return 0
        if not self._lock.acquire(blocking=False):
            return 0  # the last pass is still going
        try:
            settings = self._engine_settings()
            if settings != self._settings:  # new settings deserve another try
                self._settings, self._attempts, self._paused_until = settings, {}, 0.0
            if self._clock() < self._paused_until:
                return 0
            done = 0
            for source, clip_id in self.pending()[:limit]:
                try:
                    text = self.transcribe(source.path_for(clip_id))
                except TranscriptionError as error:
                    self._attempts[clip_id] = self._attempts.get(clip_id, 0) + 1
                    self._paused_until = self._clock() + RETRY_SECONDS
                    self.status.last_error = str(error)
                    _logger.warning("couldn't transcribe %s: %s", clip_id, error)
                    break  # most errors (no model, server down) would hit every clip
                except (OSError, KeyError) as error:
                    self._attempts[clip_id] = MAX_ATTEMPTS
                    _logger.warning("couldn't transcribe %s: %s", clip_id, error)
                    continue
                write_transcript(source, clip_id, text)
                self.status.last_error = None
                self.status.transcribed += 1
                done += 1
            return done
        finally:
            self._lock.release()
