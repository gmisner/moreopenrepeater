import json
import subprocess
from pathlib import Path

import numpy as np
import pytest

from playout.tts import (
    CommandTTS,
    TTSError,
    describe_kokoro_voice,
    detect_tts,
    format_voice_id,
    list_tts_voices,
    spell_callsign,
)
from playout.wav import encode_wav


def test_spell_callsign_spaces_characters_out():
    assert spell_callsign("w1aw", phonetic=False) == "W 1 A W"


def test_spell_callsign_uses_itu_phonetics():
    assert spell_callsign("W1AW/P", phonetic=True) == "Whiskey one Alpha Whiskey stroke Papa"


def test_format_voice_id_substitutes_the_callsign():
    assert format_voice_id("This is {callsign} repeater", "K1ABC", False) == "This is K 1 A B C repeater"


def test_format_voice_id_tolerates_stray_braces():
    assert format_voice_id("{oops} {callsign}", "N0CALL", False) == "{oops} N 0 C A L L"


def test_describe_kokoro_voice_maps_locale_and_gender():
    info = describe_kokoro_voice("bm_george")
    assert info.locale == "en-gb"
    assert info.locale_label == "British English"
    assert info.gender == "male"
    assert "George" in info.label


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
    assert argv[0] == "/usr/bin/say"
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


def test_kokoro_puts_text_after_double_dash_and_passes_speed():
    calls: list = []
    engine = detect_tts(which=lambda b: "/usr/bin/kokoro" if b == "kokoro" else None)
    engine.run = _fake_run_writing(np.zeros(10, dtype=np.float32), 24000, calls)

    engine.synthesize("-v evil", voice="am_adam", speed=1.25)

    argv = calls[0]
    assert argv[:2] == ["/usr/bin/kokoro", "speak"]
    assert argv[argv.index("-v") + 1] == "am_adam"
    assert argv[argv.index("--speed") + 1] == "1.25"
    assert argv[argv.index("--lang") + 1] == "en-us"
    assert argv[argv.index("--") + 1] == "-v evil"


def test_resolve_tts_binary_checks_venv_sibling(tmp_path, monkeypatch):
    from playout import tts as tts_mod

    fake_python = tmp_path / "python"
    fake_python.write_text("")
    fake_python.chmod(0o755)
    kokoro = tmp_path / "kokoro"
    kokoro.write_text("")
    kokoro.chmod(0o755)
    monkeypatch.setattr(tts_mod.sys, "executable", str(fake_python))

    assert tts_mod.resolve_tts_binary("kokoro", which=lambda _name: None) == str(kokoro)


def test_detect_tts_prefers_kokoro_then_say_then_espeak_ng():
    assert detect_tts(which=lambda b: b in ("kokoro", "say", "espeak-ng")).name == "kokoro"
    assert detect_tts(which=lambda b: b in ("say", "espeak-ng")).name == "say"
    assert detect_tts(which=lambda b: b in ("espeak-ng", "pico2wave")).name == "espeak-ng"
    assert detect_tts(which=lambda b: None) is None


def test_list_tts_voices_for_kokoro():
    engine = detect_tts(which=lambda b: "/usr/bin/kokoro" if b == "kokoro" else None)

    def run(argv, **kwargs):
        assert argv == ["/usr/bin/kokoro", "voices", "--json"]
        return subprocess.CompletedProcess(
            argv, 0, stdout=json.dumps({"voices": ["af_heart", "bm_george"]}), stderr=""
        )

    voices = list_tts_voices(engine, run=run)
    assert [voice.id for voice in voices] == ["af_heart", "bm_george"]
    assert voices[1].locale_label == "British English"


def test_list_tts_voices_empty_for_other_engines():
    engine = detect_tts(which=lambda b: "/usr/bin/say" if b == "say" else None)
    assert list_tts_voices(engine) == []


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
