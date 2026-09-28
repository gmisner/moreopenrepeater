import subprocess
from pathlib import Path

import numpy as np
import pytest

from playout.tts import CommandTTS, TTSError, detect_tts, format_voice_id, spell_callsign
from playout.wav import encode_wav


def test_spell_callsign_spaces_characters_out():
    assert spell_callsign("w1aw", phonetic=False) == "W 1 A W"


def test_spell_callsign_uses_itu_phonetics():
    assert spell_callsign("W1AW/P", phonetic=True) == "Whiskey one Alpha Whiskey stroke Papa"


def test_format_voice_id_substitutes_the_callsign():
    assert format_voice_id("This is {callsign} repeater", "K1ABC", False) == "This is K 1 A B C repeater"


def test_format_voice_id_tolerates_stray_braces():
    assert format_voice_id("{oops} {callsign}", "N0CALL", False) == "{oops} N 0 C A L L"


def _fake_run_writing(samples: np.ndarray, rate: int, calls: list):
    def run(argv, **kwargs):
        calls.append(argv)
        out = Path(argv[argv.index("-o") + 1] if "-o" in argv else argv[argv.index("-w") + 1])
        out.write_bytes(encode_wav(samples, rate))
        return subprocess.CompletedProcess(argv, 0)

    return run


def test_say_engine_passes_text_via_file_not_argv():
    calls: list = []
    engine = detect_tts(which=lambda b: "/usr/bin/say" if b == "say" else None)
    engine.run = _fake_run_writing(np.zeros(100, dtype=np.float32), 22050, calls)

    samples, rate = engine.synthesize("-v evil", voice="Samantha")

    argv = calls[0]
    assert argv[0] == "say"
    assert "-v evil" not in argv  # announcement text can't smuggle in flags
    assert argv[argv.index("-v") + 1] == "Samantha"
    assert "-f" in argv
    assert rate == 22050 and len(samples) == 100


def test_pico2wave_prefixes_text_so_it_cannot_be_parsed_as_a_flag():
    calls: list = []
    engine = detect_tts(which=lambda b: "/usr/bin/pico2wave" if b == "pico2wave" else None)
    engine.run = _fake_run_writing(np.zeros(10, dtype=np.float32), 16000, calls)

    engine.synthesize("-x text")

    assert calls[0][-1] == " -x text"


def test_detect_tts_prefers_say_then_espeak_ng():
    assert detect_tts(which=lambda b: b in ("say", "espeak-ng")).name == "say"
    assert detect_tts(which=lambda b: b in ("espeak-ng", "pico2wave")).name == "espeak-ng"
    assert detect_tts(which=lambda b: None) is None


def test_engine_failure_raises_tts_error():
    def failing_run(argv, **kwargs):
        raise subprocess.CalledProcessError(1, argv)

    engine = CommandTTS("say", lambda *a: ["say"], run=failing_run)
    with pytest.raises(TTSError):
        engine.synthesize("hello")


@pytest.mark.skipif(detect_tts() is None, reason="no local text-to-speech engine installed")
def test_real_installed_engine_produces_audible_speech():
    samples, rate = detect_tts().synthesize("W 1 A W repeater")
    assert rate >= 8000
    assert len(samples) / rate > 0.5
    assert np.max(np.abs(samples)) > 0.05
