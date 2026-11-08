"""Local text-to-speech via whichever command-line engine is installed.

No cloud service: a repeater site often has flaky internet, and an ID must
never fail because an API is down. Supported engines, in preference order:
`kokoro` (neural), macOS `say`, `espeak-ng`/`espeak` (the usual Raspberry Pi
default), and `pico2wave` (SVOX Pico).

Text is handed to `say`/`espeak` through a file rather than argv so that
announcement text starting with "-" can't be parsed as a command-line flag;
`pico2wave` has no file option, so its text is prefixed with a space
instead (getopt only treats arguments *starting* with "-" as options).
`kokoro` gets the text after `--` so argparse won't treat it as a flag.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional, Protocol

import numpy as np

from .kokoro_service import DEFAULT_KOKORO_SERVICE_URL
from .wav import decode_wav

_logger = logging.getLogger("moreopenrepeater.tts")
TIMEOUT_SECONDS = 30
# Fail over to espeak before a slow Pi speaks long enough to starve the controller.
KOKORO_SPEAK_TIMEOUT_SECONDS = 20
DEFAULT_TTS_SPEED = 1.0
MIN_TTS_SPEED = 0.5
MAX_TTS_SPEED = 2.0
# Prefer the long-lived localhost service so each speak does not reload ONNX.
KOKORO_SERVICE_MODE = "required"

ITU_PHONETIC = {
    "A": "Alpha", "B": "Bravo", "C": "Charlie", "D": "Delta", "E": "Echo", "F": "Foxtrot",
    "G": "Golf", "H": "Hotel", "I": "India", "J": "Juliett", "K": "Kilo", "L": "Lima",
    "M": "Mike", "N": "November", "O": "Oscar", "P": "Papa", "Q": "Quebec", "R": "Romeo",
    "S": "Sierra", "T": "Tango", "U": "Uniform", "V": "Victor", "W": "Whiskey", "X": "X-ray",
    "Y": "Yankee", "Z": "Zulu",
    "0": "zero", "1": "one", "2": "two", "3": "three", "4": "four",
    "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "nine",
    "/": "stroke", "-": "dash",
}

# Kokoro voice ids are ``{lang}{gender}_{name}`` (e.g. af_heart, bm_george).
KOKORO_LOCALES = {
    "a": ("en-us", "American English"),
    "b": ("en-gb", "British English"),
    "e": ("es-es", "Spanish"),
    "f": ("fr-fr", "French"),
    "h": ("hi-in", "Hindi"),
    "i": ("it-it", "Italian"),
    "j": ("ja-jp", "Japanese"),
    "p": ("pt-br", "Brazilian Portuguese"),
    "z": ("zh-cn", "Mandarin Chinese"),
}
_KOKORO_VOICE_RE = re.compile(r"^[a-z]{2}_[a-z0-9_]+$")
# Used when `kokoro voices` can't run (models still downloading, etc.).
_KOKORO_BUILTIN_VOICES = (
    "af_alloy", "af_aoede", "af_bella", "af_heart", "af_jessica", "af_kore",
    "af_nicole", "af_nova", "af_river", "af_sarah", "af_sky",
    "am_adam", "am_echo", "am_eric", "am_fenrir", "am_liam", "am_michael",
    "am_onyx", "am_puck", "am_santa",
    "bf_alice", "bf_emma", "bf_isabella", "bf_lily",
    "bm_daniel", "bm_fable", "bm_george", "bm_lewis",
)


def spell_callsign(callsign: str, phonetic: bool) -> str:
    """Space the characters out so TTS spells the call instead of trying to
    pronounce "W1AW" as a word; optionally use ITU phonetics."""
    chars = [c for c in callsign.upper() if not c.isspace()]
    if phonetic:
        return " ".join(ITU_PHONETIC.get(c, c) for c in chars)
    return " ".join(chars)


def spoken_time(now: datetime) -> str:
    hour = now.hour % 12 or 12
    minutes = "o'clock" if now.minute == 0 else f"{now.minute:02d}" if now.minute >= 10 else f"oh {now.minute}"
    return f"{hour} {minutes} {'A M' if now.hour < 12 else 'P M'}"


def format_voice_id(template: str, callsign: str, phonetic: bool) -> str:
    # str.replace rather than str.format: a stray "{" typed into the
    # dashboard shouldn't crash every ID.
    return template.replace("{callsign}", spell_callsign(callsign, phonetic)).strip()


def clamp_tts_speed(speed: float) -> float:
    return max(MIN_TTS_SPEED, min(MAX_TTS_SPEED, float(speed)))


@dataclass(frozen=True)
class TTSVoiceInfo:
    id: str
    label: str
    locale: str
    locale_label: str
    gender: str  # "female", "male", or ""


def describe_kokoro_voice(voice_id: str) -> TTSVoiceInfo:
    lang_key = voice_id[:1] if voice_id else ""
    gender_key = voice_id[1:2] if len(voice_id) > 1 else ""
    locale, locale_label = KOKORO_LOCALES.get(lang_key, ("", "Other"))
    gender = "female" if gender_key == "f" else "male" if gender_key == "m" else ""
    name = voice_id.split("_", 1)[-1].replace("_", " ").title() if "_" in voice_id else voice_id
    label = f"{name} ({gender})" if gender else name
    return TTSVoiceInfo(id=voice_id, label=label, locale=locale, locale_label=locale_label, gender=gender)


def lang_for_kokoro_voice(voice: str) -> str:
    if not voice:
        return "en-us"
    return describe_kokoro_voice(voice).locale or "en-us"


def sanitize_kokoro_voice(voice: str) -> str:
    """Drop leftover espeak/say voice names so Kokoro uses its default instead."""
    voice = voice.strip()
    if not voice:
        return ""
    if _KOKORO_VOICE_RE.fullmatch(voice):
        return voice
    _logger.warning("ignoring TTS voice %r (not a Kokoro id like af_heart); using the default", voice)
    return ""


class TTSEngine(Protocol):
    name: str

    def synthesize(self, text: str, voice: str = "", speed: float = DEFAULT_TTS_SPEED) -> tuple[np.ndarray, int]: ...


class TTSError(RuntimeError):
    pass


@dataclass
class CommandTTS:
    """Runs `build_argv(text_file, out_wav, text, voice, speed)` and reads the WAV it writes."""

    name: str
    build_argv: Callable[[Path, Path, str, str, float], list[str]]
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run
    # Absolute path when the binary isn't on PATH (e.g. kokoro in the venv).
    binary: str = ""
    # When Kokoro fails or times out on a hot Pi, use espeak/pico instead of silence.
    fallback: Optional["CommandTTS"] = None
    # Serialize Kokoro speaks so a cold service does not get stampeded.
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def synthesize(
        self, text: str, voice: str = "", speed: float = DEFAULT_TTS_SPEED
    ) -> tuple[np.ndarray, int]:
        if self.name == "kokoro":
            voice = sanitize_kokoro_voice(voice)
            with self._lock:
                return self._synthesize_kokoro(text, voice, speed)
        return self._synthesize_once(text, voice, speed)

    def _synthesize_kokoro(
        self, text: str, voice: str, speed: float
    ) -> tuple[np.ndarray, int]:
        try:
            return self._synthesize_once(text, voice, speed)
        except TTSError as error:
            # Unknown-but-plausible ids: retry the Kokoro default. Don't retry timeouts.
            if voice and isinstance(error.__cause__, subprocess.CalledProcessError):
                _logger.warning("Kokoro voice %r failed; retrying with the default voice", voice)
                try:
                    return self._synthesize_once(text, "", speed)
                except TTSError as retry_error:
                    error = retry_error
            if self.fallback is not None:
                _logger.warning("Kokoro failed (%s); falling back to %s", error, self.fallback.name)
                # espeak voice names are not Kokoro ids — leave blank.
                return self.fallback.synthesize(text, "", speed=speed)
            raise error

    def _synthesize_once(
        self, text: str, voice: str, speed: float
    ) -> tuple[np.ndarray, int]:
        with tempfile.TemporaryDirectory(prefix="mor-tts-") as tmp:
            text_file = Path(tmp) / "text.txt"
            out_wav = Path(tmp) / "speech.wav"
            text_file.write_text(text)
            argv = self.build_argv(text_file, out_wav, text, voice, clamp_tts_speed(speed))
            if self.binary:
                argv = [self.binary, *argv[1:]]
            timeout = KOKORO_SPEAK_TIMEOUT_SECONDS if self.name == "kokoro" else TIMEOUT_SECONDS
            try:
                self.run(argv, check=True, capture_output=True, timeout=timeout)
            except subprocess.TimeoutExpired as error:
                raise TTSError(
                    f"{self.name} timed out after {timeout}s (is kokoro serve running?)"
                ) from error
            except subprocess.CalledProcessError as error:
                detail = (error.stderr or error.stdout or b"").decode("utf-8", "replace").strip()
                message = f"{self.name} failed: {error}"
                if detail:
                    message = f"{message}; {detail}"
                raise TTSError(message) from error
            except OSError as error:
                raise TTSError(f"{self.name} failed: {error}") from error
            if not out_wav.exists():
                raise TTSError(f"{self.name} produced no audio")
            return decode_wav(out_wav.read_bytes())


def _say_argv(text_file: Path, out_wav: Path, text: str, voice: str, speed: float) -> list[str]:
    return ["say", "-o", str(out_wav), "--data-format=LEI16@22050", *(["-v", voice] if voice else []), "-f", str(text_file)]


def _espeak_argv(binary: str) -> Callable[[Path, Path, str, str, float], list[str]]:
    def build(text_file: Path, out_wav: Path, text: str, voice: str, speed: float) -> list[str]:
        return [binary, "-w", str(out_wav), *(["-v", voice] if voice else []), "-f", str(text_file)]

    return build


def _pico_argv(text_file: Path, out_wav: Path, text: str, voice: str, speed: float) -> list[str]:
    return ["pico2wave", "-w", str(out_wav), *(["-l", voice] if voice else []), " " + text]


def _kokoro_argv(text_file: Path, out_wav: Path, text: str, voice: str, speed: float) -> list[str]:
    # Prefer the long-lived localhost service (started by the API lifespan) so
    # each speak does not cold-load the ONNX model on a Pi.
    service_url = os.environ.get("KOKORO_SERVICE_URL", DEFAULT_KOKORO_SERVICE_URL)
    return [
        "kokoro",
        "speak",
        "-o",
        str(out_wav),
        "-f",
        "wav",
        "--service",
        KOKORO_SERVICE_MODE,
        "--service-url",
        service_url,
        "--speed",
        f"{speed:g}",
        "--lang",
        lang_for_kokoro_voice(voice),
        *(["-v", voice] if voice else []),
        "--",
        text,
    ]


def resolve_tts_binary(name: str, which: Callable[[str], Optional[str]] = shutil.which) -> Optional[str]:
    """PATH first, then the binary next to this process's Python (the venv)."""
    found = which(name)
    if found:
        return found
    sibling = Path(sys.executable).resolve().parent / name
    if sibling.is_file() and os.access(sibling, os.X_OK):
        return str(sibling)
    return None


_ENGINES: tuple[tuple[str, Callable[[str], CommandTTS]], ...] = (
    ("kokoro", lambda path: CommandTTS("kokoro", _kokoro_argv, binary=path)),
    ("say", lambda path: CommandTTS("say", _say_argv, binary=path)),
    ("espeak-ng", lambda path: CommandTTS("espeak-ng", _espeak_argv(path), binary=path)),
    ("espeak", lambda path: CommandTTS("espeak", _espeak_argv(path), binary=path)),
    ("pico2wave", lambda path: CommandTTS("pico2wave", _pico_argv, binary=path)),
)


def detect_tts(which: Callable[[str], Optional[str]] = shutil.which) -> Optional[TTSEngine]:
    engines: dict[str, CommandTTS] = {}
    for name, factory in _ENGINES:
        path = resolve_tts_binary(name, which=which)
        if path:
            engines[name] = factory(path)
    if not engines:
        _logger.warning(
            "no text-to-speech engine found (install kokoro-cli, espeak-ng, or libttspico-utils); voice IDs fall back to CW"
        )
        return None
    primary_name = next(name for name, _factory in _ENGINES if name in engines)
    primary = engines[primary_name]
    if primary_name == "kokoro":
        for fallback_name in ("espeak-ng", "espeak", "pico2wave"):
            if fallback_name in engines:
                primary.fallback = engines[fallback_name]
                _logger.info(
                    "text-to-speech engine: %s (%s), fallback %s",
                    primary_name,
                    primary.binary,
                    fallback_name,
                )
                return primary
    _logger.info("text-to-speech engine: %s (%s)", primary_name, primary.binary)
    return primary


def list_tts_voices(engine: Optional[TTSEngine]) -> list[TTSVoiceInfo]:
    """Voices the current engine can use. Only Kokoro is enumerated today.

    Uses a built-in id list instead of `kokoro voices`: spawning the CLI on a
    Pi can peg the CPU for several seconds and starve the audio engine.
    """
    if engine is None or engine.name != "kokoro":
        return []
    return [describe_kokoro_voice(voice_id) for voice_id in _KOKORO_BUILTIN_VOICES]
