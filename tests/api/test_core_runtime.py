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
