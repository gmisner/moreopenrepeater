"""Piper in its own container: the core uses it over the private network and never starts a Piper of its own."""
import io
import json
import re
import shutil
import subprocess
from pathlib import Path
from urllib.error import URLError

import pytest
import yaml

from api.core_runtime import piper_service_for
from playout.piper_service import wait_until_ready
from playout.tts import (
    CommandTTS,
    TTSError,
    _piper_argv,
    detect_tts,
    ensure_piper_voice_remote,
    piper_service_external,
)
from playout.wav import encode_wav

DOCKER = Path(__file__).resolve().parents[2] / "docker"
COMPOSE = yaml.safe_load((DOCKER / "compose.yaml").read_text())


@pytest.fixture
def external(monkeypatch):
    monkeypatch.setenv("PIPER_SERVICE_URL", "http://piper:8766")
    monkeypatch.setenv("PIPER_SERVICE_EXTERNAL", "1")
    monkeypatch.setenv("PIPER_DATA_DIR", "")


class Reply(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def wav():
    return encode_wav(np_zeros(), 22050)


def np_zeros():
    import numpy as np

    return np.zeros(2205, dtype=np.float32)


# -- the core ----------------------------------------------------------------------------------------------------


def test_the_service_is_external_only_when_it_is_said_so_and_named(monkeypatch):
    monkeypatch.delenv("PIPER_SERVICE_EXTERNAL", raising=False)
    monkeypatch.setenv("PIPER_SERVICE_URL", "http://piper:8766")
    assert not piper_service_external()
    monkeypatch.setenv("PIPER_SERVICE_EXTERNAL", "1")
    assert piper_service_external()
    monkeypatch.setenv("PIPER_SERVICE_URL", "off")  # said external, but no service named: not external
    assert not piper_service_external()
    monkeypatch.setenv("PIPER_SERVICE_URL", "http://piper:8766")
    monkeypatch.setenv("PIPER_SERVICE_EXTERNAL", "0")
    assert not piper_service_external()


def test_piper_is_the_engine_with_no_piper_program_in_the_core_and_espeak_is_its_fallback(external):
    found = {"espeak-ng": "/usr/bin/espeak-ng"}
    engine = detect_tts(which=found.get)
    assert engine.name == "piper" and "piper:8766" in engine.binary
    assert engine.fallback is not None and engine.fallback.name == "espeak-ng"


def test_without_the_external_setting_no_piper_program_means_no_piper(monkeypatch):
    monkeypatch.delenv("PIPER_SERVICE_EXTERNAL", raising=False)
    engine = detect_tts(which={"espeak-ng": "/usr/bin/espeak-ng"}.get)
    assert engine.name == "espeak-ng"


def test_the_core_does_not_start_or_own_an_external_piper(external):
    class Renderer:
        tts = CommandTTS("piper", _piper_argv, binary="x")

    assert piper_service_for(Renderer()) is None


def test_speech_comes_from_the_service_with_the_voice_asked_for(external):
    seen = []

    def urlopen(request, timeout=None):
        seen.append((request.full_url, request.data))
        if request.full_url.endswith("/voices"):
            return Reply(json.dumps({"en_US-amy-low": {}}).encode())
        return Reply(wav())

    tts = CommandTTS("piper", _piper_argv, binary="service", urlopen=urlopen)
    samples, rate = tts.synthesize("this is a test", "en_US-amy-low")
    assert rate == 22050 and len(samples) == 2205
    assert seen[0][0] == "http://piper:8766/voices" and seen[1][0] == "http://piper:8766/synthesize"
    assert json.loads(seen[1][1])["voice"] == "en_US-amy-low"


def test_a_voice_the_service_does_not_have_is_downloaded_by_the_service_first(external):
    seen = []

    def urlopen(request, timeout=None):
        seen.append(request.full_url)
        return Reply(json.dumps({"en_US-amy-low": {}}).encode() if request.full_url.endswith("/voices") else b"en_GB-cori-high")

    ensure_piper_voice_remote("en_GB-cori-high", urlopen=urlopen)
    assert seen == ["http://piper:8766/voices", "http://piper:8766/download"]
    seen.clear()
    ensure_piper_voice_remote("en_US-amy-low", urlopen=urlopen)  # already there: nothing to download
    assert seen == ["http://piper:8766/voices"]


def test_a_service_that_is_down_falls_back_to_espeak_and_never_tries_a_piper_program_the_core_does_not_have(external):
    calls = []

    def urlopen(request, timeout=None):
        raise URLError("connection refused")

    def run(argv, **kwargs):
        calls.append(argv)
        raise FileNotFoundError(argv[0])

    fallback = CommandTTS("espeak-ng", lambda *a: ["espeak-ng"], run=lambda argv, **kw: calls.append(argv))
    fallback.synthesize = lambda text, voice="", speed=1.0: ("fallback", 22050)  # what matters is that it is reached
    tts = CommandTTS("piper", _piper_argv, binary="service", urlopen=urlopen, run=run, fallback=fallback)
    assert tts.synthesize("hello", "en_US-amy-low") == ("fallback", 22050)
    assert calls == []  # no `piper` CLI was run: it is not in this image


def test_with_no_fallback_a_down_service_is_a_tts_error_not_a_crash(external):
    def urlopen(request, timeout=None):
        raise URLError("refused")

    with pytest.raises(TTSError):
        CommandTTS("piper", _piper_argv, binary="service", urlopen=urlopen).synthesize("hello", "en_US-amy-low")


def test_waiting_for_the_service_is_bounded_and_never_raises():
    now = [0.0]

    def clock():
        return now[0]

    def sleep(seconds):
        now[0] += seconds

    def down(request, timeout=None):
        raise URLError("refused")

    assert wait_until_ready("http://piper:8766", 5, urlopen=down, sleep=sleep, clock=clock) is False
    assert now[0] >= 5
    answers = iter([URLError("not yet"), URLError("not yet"), Reply(b'{"voice": {}}')])

    def comes_up(request, timeout=None):
        answer = next(answers)
        if isinstance(answer, Exception):
            raise answer
        return answer

    assert wait_until_ready("http://piper:8766", 60, urlopen=comes_up, sleep=sleep, clock=clock) is True


# -- compose ----------------------------------------------------------------------------------------------------


def test_piper_is_on_by_default_private_locked_down_and_has_no_device():
    piper = COMPOSE["services"]["piper"]
    assert "profiles" not in piper and "ports" not in piper and "devices" not in piper and "network_mode" not in piper
    assert piper["networks"] == ["services"] and piper["user"] == "999:999" and piper["read_only"] is True
    assert piper["cap_drop"] == ["ALL"] and "no-new-privileges:true" in piper["security_opt"]
    assert piper["volumes"] == ["piper-voices:/voices"] and "piper-voices" in COMPOSE["volumes"]
    assert "/info" in " ".join(piper["healthcheck"]["test"])


def test_the_core_asks_it_over_the_private_network_holds_no_piper_and_does_not_wait_on_it_to_start():
    core = COMPOSE["services"]["core"]
    assert core["build"]["args"]["WITH_PIPER"] == "0"
    environment = core["environment"]
    assert environment["PIPER_SERVICE_URL"] == "http://piper:8766" and environment["PIPER_SERVICE_EXTERNAL"] == "1"
    assert "piper" not in str(core.get("depends_on", ""))  # the repeater never waits for a voice; the core waits a bounded time itself


def test_the_image_pins_the_version_and_the_base_and_runs_as_the_shared_user():
    text = (DOCKER / "Dockerfile.piper").read_text()
    assert re.search(r"^FROM python:3\.13-slim-trixie@sha256:[0-9a-f]{64}$", text, re.M)
    assert re.search(r"ARG PIPER_VERSION=\d+\.\d+\.\d+\n", text) and "USER 999:999" in text
    assert "0.0.0.0" in text and "--data-dir" in text and "/voices" in text


docker = shutil.which("docker")


@pytest.mark.skipif(docker is None, reason="needs docker compose to merge the files")
def test_the_host_network_layout_puts_piper_back_in_the_core():
    def merged(*files):
        command = ["docker", "compose"]
        for name in files:
            command += ["-f", name]
        result = subprocess.run(command + ["config", "--format", "json"], cwd=DOCKER, capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)["services"]

    old = merged("compose.yaml", "compose.host-network.yaml")
    assert old["core"]["build"]["args"]["WITH_PIPER"] == "1"
    assert old["core"]["environment"]["PIPER_SERVICE_URL"] == "http://127.0.0.1:8766"
    assert old["core"]["environment"]["PIPER_SERVICE_EXTERNAL"] == "0"
    assert "piper" not in old  # its profile is one nobody starts, so `compose up` leaves it out in that layout
    assert "piper" in merged("compose.yaml")


def test_a_voice_that_fell_back_is_a_named_warning_with_both_engines_and_the_error_type(external, structured_events):
    def urlopen(request, timeout=None):
        raise URLError("secret-host-detail")

    fallback = CommandTTS("espeak-ng", lambda *a: ["espeak-ng"], run=lambda argv, **kw: None)
    fallback.synthesize = lambda text, voice="", speed=1.0: ("fallback", 22050)
    tts = CommandTTS("piper", _piper_argv, binary="service", urlopen=urlopen, run=lambda argv, **kw: None, fallback=fallback)
    assert tts.synthesize("a secret sentence", "en_US-amy-low") == ("fallback", 22050)
    used = structured_events.named("tts.fallback_used")
    assert used and used[-1].severity.name == "WARNING" and used[-1].error_code == "tts.fallback"
    assert used[-1].context == {"engine": "piper", "fallback": "espeak-ng", "exception": "TTSError"}
    assert "a secret sentence" not in used[-1].model_dump_json()  # never the text that was being spoken


def test_no_engine_at_all_is_a_named_warning(structured_events):
    assert detect_tts(which=lambda binary: None) is None
    missing = structured_events.named("tts.no_engine")
    assert len(missing) == 1 and missing[0].severity.name == "WARNING" and missing[0].error_code == "tts.no_engine"
    assert missing[0].context == {}


def test_the_api_is_told_where_the_piper_service_is_so_the_dashboard_can_preview_and_list_voices():
    compose = yaml.safe_load((DOCKER / "compose.yaml").read_text())
    api_env = compose["services"]["api"]["environment"]
    core_env = compose["services"]["core"]["environment"]
    assert api_env["PIPER_SERVICE_URL"] == core_env["PIPER_SERVICE_URL"] == "http://piper:8766"
    assert str(api_env["PIPER_SERVICE_EXTERNAL"]) == "1"
    assert "piper" not in (compose["services"]["api"].get("devices") or [])  # it asks over the network, it holds nothing of Piper's


def test_a_slow_voice_download_does_not_hold_up_speech_in_a_voice_the_service_already_has(external):
    """Review finding: `ensure_piper_voice_remote` used to run under the speak lock, so a download of one voice (up to
    180 s) held up a station ID spoken in another."""
    import threading

    downloading, release = threading.Event(), threading.Event()

    def urlopen(request, timeout=None):
        url = request.full_url
        if url.endswith("/voices"):
            return Reply(json.dumps({"en_US-amy-low": {}}).encode())
        if url.endswith("/download"):
            downloading.set()
            assert release.wait(10)
            return Reply(b"ok")
        return Reply(wav())

    tts = CommandTTS("piper", _piper_argv, binary="service", urlopen=urlopen)
    slow = threading.Thread(target=lambda: tts.synthesize("preview", "en_GB-cori-high"))
    slow.start()
    try:
        assert downloading.wait(5)
        result = []
        station_id = threading.Thread(target=lambda: result.append(tts.synthesize("this is the id", "en_US-amy-low")))
        station_id.start()
        station_id.join(timeout=3)
        assert not station_id.is_alive(), "the ID was held up by another voice's download"
        assert result and result[0][1] == 22050
    finally:
        release.set()
        slow.join(timeout=10)


def test_two_speakers_asking_for_the_same_missing_voice_download_it_once(external):
    import threading

    downloads, present = [], set()

    def urlopen(request, timeout=None):
        url = request.full_url
        if url.endswith("/voices"):
            return Reply(json.dumps({v: {} for v in present}).encode())
        if url.endswith("/download"):
            downloads.append(json.loads(request.data)["voice"])
            present.add("en_GB-cori-high")
            return Reply(b"ok")
        return Reply(wav())

    tts = CommandTTS("piper", _piper_argv, binary="service", urlopen=urlopen)
    threads = [threading.Thread(target=lambda: tts.synthesize("x", "en_GB-cori-high")) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert downloads == ["en_GB-cori-high"]
