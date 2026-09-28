"""Local text-to-speech via whichever command-line engine is installed.

No cloud service: a repeater site often has flaky internet, and an ID must
never fail because an API is down. Supported engines, in preference order:
macOS `say`, `espeak-ng`/`espeak` (the usual Raspberry Pi choice), and
`pico2wave` (SVOX Pico, a more natural-sounding Pi option).

Text is handed to `say`/`espeak` through a file rather than argv so that
announcement text starting with "-" can't be parsed as a command-line flag;
`pico2wave` has no file option, so its text is prefixed with a space
instead (getopt only treats arguments *starting* with "-" as options).
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Protocol

import numpy as np

from .wav import decode_wav

_logger = logging.getLogger("moreopenrepeater.tts")
TIMEOUT_SECONDS = 30

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


def spell_callsign(callsign: str, phonetic: bool) -> str:
    """Space the characters out so TTS spells the call instead of trying to
    pronounce "W1AW" as a word; optionally use ITU phonetics."""
    chars = [c for c in callsign.upper() if not c.isspace()]
    if phonetic:
        return " ".join(ITU_PHONETIC.get(c, c) for c in chars)
    return " ".join(chars)


def format_voice_id(template: str, callsign: str, phonetic: bool) -> str:
    # str.replace rather than str.format: a stray "{" typed into the
    # dashboard shouldn't crash every ID.
    return template.replace("{callsign}", spell_callsign(callsign, phonetic)).strip()


class TTSEngine(Protocol):
    name: str

    def synthesize(self, text: str, voice: str = "") -> tuple[np.ndarray, int]: ...


class TTSError(RuntimeError):
    pass


@dataclass
class CommandTTS:
    """Runs `build_argv(text_file, out_wav, text, voice)` and reads the WAV it writes."""

    name: str
    build_argv: Callable[[Path, Path, str, str], list[str]]
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run

    def synthesize(self, text: str, voice: str = "") -> tuple[np.ndarray, int]:
        with tempfile.TemporaryDirectory(prefix="mor-tts-") as tmp:
            text_file = Path(tmp) / "text.txt"
            out_wav = Path(tmp) / "speech.wav"
            text_file.write_text(text)
            argv = self.build_argv(text_file, out_wav, text, voice)
            try:
                self.run(argv, check=True, capture_output=True, timeout=TIMEOUT_SECONDS)
            except (OSError, subprocess.SubprocessError) as error:
                raise TTSError(f"{self.name} failed: {error}") from error
            if not out_wav.exists():
                raise TTSError(f"{self.name} produced no audio")
            return decode_wav(out_wav.read_bytes())


def _say_argv(text_file: Path, out_wav: Path, text: str, voice: str) -> list[str]:
    return ["say", "-o", str(out_wav), "--data-format=LEI16@22050", *(["-v", voice] if voice else []), "-f", str(text_file)]


def _espeak_argv(binary: str) -> Callable[[Path, Path, str, str], list[str]]:
    def build(text_file: Path, out_wav: Path, text: str, voice: str) -> list[str]:
        return [binary, "-w", str(out_wav), *(["-v", voice] if voice else []), "-f", str(text_file)]

    return build


def _pico_argv(text_file: Path, out_wav: Path, text: str, voice: str) -> list[str]:
    return ["pico2wave", "-w", str(out_wav), *(["-l", voice] if voice else []), " " + text]


_ENGINES = (
    ("say", lambda: CommandTTS("say", _say_argv)),
    ("espeak-ng", lambda: CommandTTS("espeak-ng", _espeak_argv("espeak-ng"))),
    ("espeak", lambda: CommandTTS("espeak", _espeak_argv("espeak"))),
    ("pico2wave", lambda: CommandTTS("pico2wave", _pico_argv)),
)


def detect_tts(which: Callable[[str], Optional[str]] = shutil.which) -> Optional[TTSEngine]:
    for binary, factory in _ENGINES:
        if which(binary):
            _logger.info("text-to-speech engine: %s", binary)
            return factory()
    _logger.warning("no text-to-speech engine found (install espeak-ng or libttspico-utils); voice IDs fall back to CW")
    return None
