"""`CoreRuntime` is the autonomous core's start-up apart from the web app (Stage 3 slice 5, #229)."""
import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

from api.core_runtime import AnnouncementQueueFull
from api.runtime import RuntimeBuilder

SRC = str(Path(__file__).resolve().parents[2] / "src")


def test_core_runtime_imports_no_web_framework():
    names = ("fastapi", "starlette", "api.app", "api.runtime")
    code = (
        "import sys, json; sys.path.insert(0, sys.argv[1]);"
        "import api.core_runtime;"
        f"print(json.dumps({{n: n in sys.modules for n in {names!r}}}))"
    )
    result = subprocess.run([sys.executable, "-I", "-c", code, SRC], capture_output=True, text=True, timeout=60, check=True)
    assert json.loads(result.stdout.strip().splitlines()[-1]) == dict.fromkeys(names, False)


@pytest.fixture
def runtime(monkeypatch):
    runtime = RuntimeBuilder(start_background_tick=False).build()
    monkeypatch.setattr(runtime.renderer, "render", lambda clip, config: None)  # no speech synthesis
    return runtime


def test_a_full_announcement_queue_raises_a_domain_error_in_the_core(runtime, monkeypatch):
    monkeypatch.setattr(runtime.service, "queue_announcement", lambda clip, on_linked_radio=False: False)
    with pytest.raises(AnnouncementQueueFull):
        asyncio.run(runtime.core_runtime.render_and_queue("id"))


def test_the_web_layer_still_answers_a_full_queue_with_429(runtime, monkeypatch):
    monkeypatch.setattr(runtime.service, "queue_announcement", lambda clip, on_linked_radio=False: False)
    with pytest.raises(HTTPException) as error:
        asyncio.run(runtime.render_and_queue("id"))
    assert error.value.status_code == 429
    assert "Too many announcements" in error.value.detail


def test_an_accepted_announcement_is_queued(runtime, monkeypatch):
    queued = []
    monkeypatch.setattr(
        runtime.service, "queue_announcement", lambda clip, on_linked_radio=False: queued.append((clip, on_linked_radio)) or True
    )
    asyncio.run(runtime.core_runtime.render_and_queue("id", on_linked_radio=True))
    assert queued == [("id", True)]


def test_the_watchdog_watches_the_controller_and_both_engines(runtime):
    names = [name for name, _ in runtime.watchdog._watched]
    assert names == ["controller", "audio engine", "link radio audio engine"]


def test_a_voice_server_that_will_not_start_and_one_that_is_up_are_named_events(runtime, monkeypatch, structured_events):
    from types import SimpleNamespace

    from api import core_runtime as core_runtime_module
    from playout import tts as tts_module
    from playout.kokoro_service import KokoroServiceError
    from playout.piper_service import PiperServiceError

    def broken(error):
        def start_and_wait():
            raise error("secret-detail-from-the-server")

        return SimpleNamespace(start_and_wait=start_and_wait, stop=lambda: None)

    monkeypatch.setattr(core_runtime_module, "kokoro_service_for", lambda renderer: broken(KokoroServiceError))
    monkeypatch.setattr(core_runtime_module, "piper_service_for", lambda renderer, voice="": broken(PiperServiceError))
    asyncio.run(runtime.core_runtime.start_tts())
    failed = structured_events.named("tts.service_failed")
    assert [(e.error_code, e.context) for e in failed] == [
        ("tts.service", {"engine": "kokoro", "exception": "KokoroServiceError"}),
        ("tts.service", {"engine": "piper", "exception": "PiperServiceError"}),
    ]
    assert all(e.severity.name == "ERROR" for e in failed)
    assert "secret-detail-from-the-server" not in " ".join(e.model_dump_json() for e in failed)

    monkeypatch.setattr(core_runtime_module, "kokoro_service_for", lambda renderer: None)
    monkeypatch.setattr(core_runtime_module, "piper_service_for", lambda renderer, voice="": None)
    monkeypatch.setattr(runtime.renderer, "tts", SimpleNamespace(name="piper"), raising=False)
    monkeypatch.setattr(tts_module, "piper_service_external", lambda: True)
    monkeypatch.setattr(tts_module, "piper_service_url", lambda: "http://piper:8766")
    monkeypatch.setattr(core_runtime_module, "wait_until_ready", lambda url, seconds: True)
    asyncio.run(runtime.core_runtime.start_tts())
    ready = structured_events.named("tts.service_ready")
    assert [e.context for e in ready] == [{"engine": "piper"}] and ready[0].message == "the Piper service is ready"
