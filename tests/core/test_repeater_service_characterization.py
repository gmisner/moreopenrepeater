"""Characterize the radio core through its service without an HTTP server.

These tests pin the current controller/service contract ahead of the modular
core extraction; they use simulated events, an injected clock, and fake audio.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest

from api.service import RepeaterService
from controller.config import RepeaterConfig
from controller.state_machine import IDLE, LOCKOUT, RECEIVING


class FakeAudioOutput:
    """Record output requests without opening an audio or PTT device."""

    def __init__(self) -> None:
        self.ptt: list[bool] = []
        self.clips: list[str] = []
        self.repeating: list[bool] = []

    def set_ptt(self, active: bool) -> None:
        self.ptt.append(active)

    def play(self, clip: str) -> None:
        self.clips.append(clip)

    def set_repeating(self, repeating: bool) -> None:
        self.repeating.append(repeating)


def make_service(**overrides: object) -> tuple[RepeaterService, dict[str, float], FakeAudioOutput]:
    now = {"value": 0.0}
    settings: dict[str, object] = {
        "courtesy_tone_duration": 0.2,
        "hang_time": 1.0,
        "tot_duration": 5.0,
        "id_interval": 100.0,
        "id_audio_duration": 0.5,
    }
    settings.update(overrides)
    config = RepeaterConfig(**settings)
    service = RepeaterService(config=config, clock=lambda: now["value"])
    audio = FakeAudioOutput()
    service.audio_output = audio
    return service, now, audio


def test_cos_repeats_and_releases_ptt_after_courtesy_and_hang_time() -> None:
    service, now, audio = make_service()

    service.simulate_cos(active=True)
    assert service.snapshot().state == RECEIVING
    assert service.snapshot().ptt_active is True
    assert audio.ptt == [True]

    now["value"] = 1.0
    service.simulate_cos(active=False)
    now["value"] = 1.2
    service.tick()
    assert service.snapshot().state == "hang_time"
    assert service.snapshot().ptt_active is True

    now["value"] = 2.3
    service.tick()
    assert service.snapshot().state == IDLE
    assert service.snapshot().ptt_active is False
    assert audio.ptt == [True, False]


def test_timeout_locks_out_and_clear_lockout_rekeys_a_held_carrier() -> None:
    service, now, audio = make_service(
        tot_duration=2.0,
        lockout_timeouts=1,
        lockout_window=60.0,
        lockout_clear_after=20.0,
    )
    service.simulate_cos(active=True)

    now["value"] = 2.1
    service.tick()
    assert service.snapshot().state == LOCKOUT
    assert service.snapshot().ptt_active is False
    assert audio.ptt[-1] is False

    service.clear_lockout()
    assert service.snapshot().state == RECEIVING
    assert service.snapshot().ptt_active is True
    assert audio.ptt[-1] is True


def test_idle_id_setting_plays_station_id_when_interval_expires() -> None:
    service, now, audio = make_service(id_interval=2.0, idle_id=True)

    now["value"] = 2.0
    service.tick()

    assert service.snapshot().state == "transmitting_id"
    assert service.last_clip == "id"
    assert audio.clips == ["id"]
    assert service.snapshot().ptt_active is True


def test_core_repeats_without_any_status_subscriber() -> None:
    service, now, _audio = make_service()

    service.simulate_cos(active=True)
    assert service.snapshot().ptt_active is True
    now["value"] = 1.0
    service.simulate_cos(active=False)
    now["value"] = 1.2
    service.tick()
    now["value"] = 2.3
    service.tick()

    assert service.snapshot().state == IDLE
    assert service.snapshot().ptt_active is False


def _run_isolated(code: str, *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", "-c", code],
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )


def test_controller_modules_import_without_fastapi_or_starlette() -> None:
    code = """
import importlib, pkgutil, sys
sys.path.insert(0, 'src')
import controller
for module in pkgutil.walk_packages(controller.__path__, controller.__name__ + '.'):
    importlib.import_module(module.name)
blocked = sorted(name for name in ('fastapi', 'starlette') if name in sys.modules)
assert not blocked, f'web framework modules imported: {blocked}'
"""
    result = _run_isolated(code)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.xfail(
    strict=True,
    reason="F3: api/__init__.py imports the app at import time; fixed by Stage 3 slice 2",
)
def test_importing_api_service_does_not_import_fastapi() -> None:
    # Keep the probe away from host radio devices even though importing api.service
    # currently creates the application as an api package side effect.
    env = os.environ.copy()
    env["MOREOPENREPEATER_CM108_HIDRAW"] = "off"
    env["MOREOPENREPEATER_LINK_CM108_HIDRAW"] = "off"
    code = """
import sys
sys.path.insert(0, 'src')
import api.service
assert 'fastapi' not in sys.modules, 'api.service imported fastapi'
"""
    result = _run_isolated(code, env=env)
    assert result.returncode == 0, result.stdout + result.stderr
