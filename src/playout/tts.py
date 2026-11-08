"""Local text-to-speech via whichever command-line engine is installed.

No cloud service: a repeater site often has flaky internet, and an ID must
never fail because an API is down. Supported engines, in preference order:
`piper` (neural, Pi-friendly), `kokoro` (heavier neural), macOS `say`,
`espeak-ng`/`espeak`, and `pico2wave` (SVOX Pico).

Text is handed to `say`/`espeak` through a file rather than argv so that
announcement text starting with "-" can't be parsed as a command-line flag;
`pico2wave` has no file option, so its text is prefixed with a space
instead (getopt only treats arguments *starting* with "-" as options).
`kokoro` gets text after `--`; `piper` reads text from `-i` so flags in the
announcement cannot break argv.
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
PIPER_SPEAK_TIMEOUT_SECONDS = 30
DEFAULT_TTS_SPEED = 1.0
MIN_TTS_SPEED = 0.5
MAX_TTS_SPEED = 2.0
# Prefer the long-lived localhost service so each speak does not reload ONNX.
KOKORO_SERVICE_MODE = "required"
DEFAULT_PIPER_VOICE = "en_GB-cori-high"
# Piper voice ids look like en_GB-cori-high (locale-name-quality).
_PIPER_VOICE_RE = re.compile(r"^[a-z]{2}_[A-Z]{2}-[a-z0-9_]+-(x_low|low|medium|high)$")
# Curated English set for the Identification picker (download on first use).
_PIPER_BUILTIN_VOICES = (
    "en_GB-cori-high",
    "en_GB-cori-medium",
    "en_GB-alan-medium",
    "en_GB-alba-medium",
    "en_GB-jenny_dioco-medium",
    "en_US-lessac-high",
    "en_US-lessac-medium",
    "en_US-amy-medium",
    "en_US-ryan-high",
    "en_US-ryan-medium",
    "en_US-joe-medium",
    "en_US-kristin-medium",
    "en_US-bryce-medium",
    "en_US-hfc_female-medium",
    "en_US-hfc_male-medium",
)

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


def default_piper_voice() -> str:
    return os.environ.get("PIPER_VOICE", DEFAULT_PIPER_VOICE).strip() or DEFAULT_PIPER_VOICE


def sanitize_piper_voice(voice: str) -> str:
    """Accept Piper voice ids; map Kokoro leftovers to the configured default."""
    voice = voice.strip()
    if not voice:
        return default_piper_voice()
    if _PIPER_VOICE_RE.fullmatch(voice):
        return voice
    _logger.warning("ignoring TTS voice %r (not a Piper id like en_GB-cori-high); using %s", voice, default_piper_voice())
    return default_piper_voice()


def ensure_piper_voice(voice: str, *, run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> None:
    """Download a Piper voice into PIPER_DATA_DIR when the onnx file is missing."""
    data_dir = os.environ.get("PIPER_DATA_DIR", "").strip()
    if not data_dir:
        return
    root = Path(data_dir)
    if (root / f"{voice}.onnx").is_file() and (root / f"{voice}.onnx.json").is_file():
        return
    root.mkdir(parents=True, exist_ok=True)
    _logger.info("downloading Piper voice %s into %s", voice, root)
    try:
        run(
            [sys.executable, "-m", "piper.download_voices", "--data-dir", str(root), voice],
            check=True,
            capture_output=True,
            timeout=180,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as error:
        raise TTSError(f"couldn't download Piper voice {voice}: {error}") from error


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
    # When neural TTS fails or times out on a hot Pi, use espeak/pico instead of silence.
    fallback: Optional["CommandTTS"] = None
    # Serialize neural speaks so a cold load does not get stampeded.
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def synthesize(
        self, text: str, voice: str = "", speed: float = DEFAULT_TTS_SPEED
    ) -> tuple[np.ndarray, int]:
        if self.name == "piper":
            voice = sanitize_piper_voice(voice)
            with self._lock:
                ensure_piper_voice(voice, run=self.run)
                return self._synthesize_neural(text, voice, speed)
        if self.name == "kokoro":
            voice = sanitize_kokoro_voice(voice)
            with self._lock:
                return self._synthesize_neural(text, voice, speed)
        return self._synthesize_once(text, voice, speed)

    def _synthesize_neural(
        self, text: str, voice: str, speed: float
    ) -> tuple[np.ndarray, int]:
        try:
            return self._synthesize_once(text, voice, speed)
        except TTSError as error:
            # Unknown-but-plausible Kokoro ids: retry the default. Don't retry timeouts.
            if (
                self.name == "kokoro"
                and voice
                and isinstance(error.__cause__, subprocess.CalledProcessError)
            ):
                _logger.warning("Kokoro voice %r failed; retrying with the default voice", voice)
                try:
                    return self._synthesize_once(text, "", speed)
                except TTSError as retry_error:
                    error = retry_error
            if self.fallback is not None:
                _logger.warning("%s failed (%s); falling back to %s", self.name, error, self.fallback.name)
                # espeak voice names are not neural ids — leave blank.
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
            if self.name == "kokoro":
                timeout = KOKORO_SPEAK_TIMEOUT_SECONDS
            elif self.name == "piper":
                timeout = PIPER_SPEAK_TIMEOUT_SECONDS
            else:
                timeout = TIMEOUT_SECONDS
            try:
                self.run(argv, check=True, capture_output=True, timeout=timeout)
            except subprocess.TimeoutExpired as error:
                hint = " (is kokoro serve running?)" if self.name == "kokoro" else ""
                raise TTSError(f"{self.name} timed out after {timeout}s{hint}") from error
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


def _piper_argv(text_file: Path, out_wav: Path, text: str, voice: str, speed: float) -> list[str]:
    # piper-tts (OHF): text via -i file so flags in the announcement can't break argv.
    # length-scale is inverse of speaking rate (1.0 = normal).
    length_scale = 1.0 / speed if speed else 1.0
    data_dir = os.environ.get("PIPER_DATA_DIR", "").strip()
    return [
        "piper",
        "-m",
        voice or default_piper_voice(),
        "-f",
        str(out_wav),
        "-i",
        str(text_file),
        "--length-scale",
        f"{length_scale:g}",
        *(["--data-dir", data_dir] if data_dir else []),
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
    ("piper", lambda path: CommandTTS("piper", _piper_argv, binary=path)),
    ("kokoro", lambda path: CommandTTS("kokoro", _kokoro_argv, binary=path)),
    ("say", lambda path: CommandTTS("say", _say_argv, binary=path)),
    ("espeak-ng", lambda path: CommandTTS("espeak-ng", _espeak_argv(path), binary=path)),
    ("espeak", lambda path: CommandTTS("espeak", _espeak_argv(path), binary=path)),
    ("pico2wave", lambda path: CommandTTS("pico2wave", _pico_argv, binary=path)),
)

_NEURAL_ENGINES = frozenset({"piper", "kokoro"})


def detect_tts(which: Callable[[str], Optional[str]] = shutil.which) -> Optional[TTSEngine]:
    engines: dict[str, CommandTTS] = {}
    for name, factory in _ENGINES:
        path = resolve_tts_binary(name, which=which)
        if path:
            engines[name] = factory(path)
    if not engines:
        _logger.warning(
            "no text-to-speech engine found (install piper-tts, espeak-ng, or libttspico-utils); voice IDs fall back to CW"
        )
        return None
    primary_name = next(name for name, _factory in _ENGINES if name in engines)
    primary = engines[primary_name]
    if primary_name in _NEURAL_ENGINES:
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


_PIPER_LOCALE_LABELS = {
    "en_GB": ("en-GB", "British English"),
    "en_US": ("en-US", "American English"),
}


def describe_piper_voice(voice_id: str) -> TTSVoiceInfo:
    parts = voice_id.split("-")
    locale_key = parts[0] if parts else ""
    name = parts[1].replace("_", " ").title() if len(parts) > 1 else voice_id
    quality = parts[2] if len(parts) > 2 else ""
    label = f"{name} ({quality})" if quality else name
    locale, locale_label = _PIPER_LOCALE_LABELS.get(
        locale_key,
        (locale_key.replace("_", "-") or "other", locale_key.replace("_", "-") or "Other"),
    )
    return TTSVoiceInfo(id=voice_id, label=label, locale=locale, locale_label=locale_label, gender="")


def list_tts_voices(engine: Optional[TTSEngine]) -> list[TTSVoiceInfo]:
    """Voices the current engine can use.

    Piper and Kokoro use built-in id lists so we never spawn a CLI just to
    populate the Identification dropdown (that pegged the Pi with Kokoro).
    """
    if engine is None:
        return []
    if engine.name == "piper":
        return [describe_piper_voice(voice_id) for voice_id in _PIPER_BUILTIN_VOICES]
    if engine.name == "kokoro":
        return [describe_kokoro_voice(voice_id) for voice_id in _KOKORO_BUILTIN_VOICES]
    return []
