"""`CoreRuntime` is the autonomous core's start-up apart from the web app (Stage 3 slice 5, #229)."""
import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

from api import core_runtime as core_runtime_module
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

    async def start_and_follow():
        await runtime.core_runtime.start_tts()
        await runtime.core_runtime._piper_watch  # the container is looked for in the background

    asyncio.run(start_and_follow())
    ready = structured_events.named("tts.service_ready")
    assert [e.context for e in ready] == [{"engine": "piper"}] and ready[0].message == "the Piper service is ready"


@pytest.fixture
def external_piper(runtime, monkeypatch):
    """The core with Piper in its own container; `piper_up` says whether the container answers."""
    from types import SimpleNamespace

    from playout import tts as tts_module

    probes = []
    state = {"up": False}
    cleared, warmed = [], []
    monkeypatch.setattr(core_runtime_module, "kokoro_service_for", lambda renderer: None)
    monkeypatch.setattr(core_runtime_module, "piper_service_for", lambda renderer, voice="": None)
    monkeypatch.setattr(runtime.renderer, "tts", SimpleNamespace(name="piper"), raising=False)
    monkeypatch.setattr(runtime.renderer, "clear_cache", lambda: cleared.append(True))
    monkeypatch.setattr(runtime.renderer, "warm", lambda config: warmed.append(True))
    monkeypatch.setattr(tts_module, "piper_service_external", lambda: True)
    monkeypatch.setattr(tts_module, "piper_service_url", lambda: "http://piper:8766")
    monkeypatch.setattr(core_runtime_module, "PIPER_RETRY_MAX_SECONDS", 0.01)

    def probe(url, seconds):
        probes.append(seconds)
        return state["up"]

    monkeypatch.setattr(core_runtime_module, "wait_until_ready", probe)
    return SimpleNamespace(core=runtime.core_runtime, state=state, probes=probes, cleared=cleared, warmed=warmed)


def test_start_tts_does_not_wait_for_a_piper_container_that_is_down(external_piper, structured_events, caplog):
    """Review finding: the core used to wait up to 60 s for Piper before audio started, so a stall restart with Piper
    down meant a minute of no repeating."""
    import time

    async def go():
        began = time.monotonic()
        await external_piper.core.start_tts()
        assert time.monotonic() - began < 0.5  # returned at once: audio can start
        await asyncio.sleep(0.2)  # the background look keeps going and finds it down
        assert len(external_piper.probes) > 2
        assert all(seconds == 0 for seconds in external_piper.probes)  # one try each, never a long wait
        assert not structured_events.named("tts.service_ready")
        external_piper.state["up"] = True
        await external_piper.core._piper_watch
        await external_piper.core.stop_tts()

    asyncio.run(go())
    assert [e.context for e in structured_events.named("tts.service_ready")] == [{"engine": "piper"}]


def test_clips_spoken_by_the_fallback_while_piper_was_down_are_rendered_again_when_it_answers(external_piper):
    async def go():
        await external_piper.core.start_tts()
        await asyncio.sleep(0.05)
        assert external_piper.cleared == [] and external_piper.warmed == []  # nothing is redone while it is down
        external_piper.state["up"] = True
        await external_piper.core._piper_watch

    asyncio.run(go())
    assert external_piper.cleared == [True] and external_piper.warmed == [True]


def test_a_piper_that_is_already_up_leaves_the_cached_clips_alone(external_piper):
    external_piper.state["up"] = True

    async def go():
        await external_piper.core.start_tts()
        await external_piper.core._piper_watch

    asyncio.run(go())
    assert external_piper.cleared == [] and external_piper.warmed == []


def test_stopping_the_core_stops_the_look_for_piper(external_piper):
    async def go():
        await external_piper.core.start_tts()
        watch = external_piper.core._piper_watch
        await external_piper.core.stop_tts()
        await asyncio.gather(watch, return_exceptions=True)
        assert watch.cancelled() and external_piper.core._piper_watch is None

    asyncio.run(go())
