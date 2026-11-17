"""The core running alone: no web app, local config copy, nothing else up
(Stage 3 slice 6, #229). Simulation only: no devices, no Pi, no network."""
import asyncio
import dataclasses
import json
import re
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from core.build import build_core
from core.config_boundary import CoreConfigError, check_config, load_config, read_snapshot
from core.main import EXIT_CONFIG, main, serve
from controller.config import RepeaterConfig

ROOT = Path(__file__).resolve().parents[2]
SRC = str(ROOT / "src")


def write_state(root: Path, **config) -> Path:
    path = root / "state.json"
    path.write_text(json.dumps({"config": {"callsign": "W1AW", "audio_enabled": False, **config}}))
    return path


def env_for(root: Path) -> dict:
    return {"MOREOPENREPEATER_DATA_DIR": str(root), "MOREOPENREPEATER_LOG_PATH": str(root / "core.log")}


@pytest.fixture(autouse=True)
def no_real_tts(monkeypatch):
    """Never start a text-to-speech server from a test; a developer machine may have one."""
    from api import core_runtime

    monkeypatch.setattr(core_runtime, "kokoro_service_for", lambda renderer: None)
    monkeypatch.setattr(core_runtime, "piper_service_for", lambda renderer, voice="": None)


# --- no web framework ---------------------------------------------------------

@pytest.mark.parametrize("module", ["core", "core.config_boundary", "core.build", "core.main", "core.control"])
def test_the_core_imports_no_web_framework(module):
    names = ("fastapi", "starlette", "api.app", "api.runtime")
    code = (
        "import sys, json; sys.path.insert(0, sys.argv[1]);"
        f"import {module};"
        f"print(json.dumps({{n: n in sys.modules for n in {names!r}}}))"
    )
    result = subprocess.run([sys.executable, "-I", "-c", code, SRC], capture_output=True, text=True, timeout=60, check=True)
    assert json.loads(result.stdout.strip().splitlines()[-1]) == dict.fromkeys(names, False)


# --- the configuration boundary -----------------------------------------------

def test_a_missing_state_file_refuses_to_start(tmp_path):
    with pytest.raises(CoreConfigError, match="no saved configuration"):
        read_snapshot(tmp_path / "state.json")


def test_an_unparsable_state_file_refuses_to_start_and_is_left_alone(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{not json")
    with pytest.raises(CoreConfigError, match="not valid JSON"):
        read_snapshot(path)
    assert path.read_text() == "{not json"  # not moved aside, not rewritten
    assert not list(tmp_path.glob("*.corrupt"))


def test_a_state_file_that_is_not_an_object_refuses_to_start(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("[1, 2]")
    with pytest.raises(CoreConfigError):
        read_snapshot(path)


@pytest.mark.parametrize("callsign", ["", "   "])
def test_a_blank_callsign_refuses_to_start(tmp_path, callsign):
    write_state(tmp_path, callsign=callsign)
    with pytest.raises(CoreConfigError, match="no callsign"):
        load_config(tmp_path / "state.json")


@pytest.mark.parametrize(
    "field,bad",
    [
        ("tot_duration", 0),
        ("tot_duration", -5),
        ("tot_duration", "soon"),
        ("tot_duration", float("nan")),
        ("id_interval", 0),
        ("hang_time", -1),
        ("link_radio_timeout", 0),
        ("net_tot_duration", -1),
        ("autopatch_max_call_seconds", 0),
        ("tot_duration", True),
    ],
)
def test_an_unsafe_number_is_replaced_by_its_default_and_reported(field, bad):
    config = dataclasses.replace(RepeaterConfig(callsign="W1AW"), **{field: bad})
    fixed, notes = check_config(config)
    assert getattr(fixed, field) == getattr(RepeaterConfig(), field)
    assert len(notes) == 1 and notes[0].startswith(field)


def test_good_settings_are_left_alone():
    config = RepeaterConfig(callsign="W1AW", tot_duration=240.0, hang_time=0.0)
    fixed, notes = check_config(config)
    assert fixed == config and notes == []


# --- building and running alone -----------------------------------------------

def test_the_core_builds_from_the_local_copy_and_keeps_its_macros_and_settings(tmp_path):
    write_state(tmp_path, tot_duration=240.0, id_interval=900.0)
    app = build_core(env_for(tmp_path))
    assert app.service.saved_config.callsign == "W1AW"
    assert app.service.saved_config.tot_duration == 240.0
    assert app.config_notes == []


def test_an_unsafe_saved_number_is_repaired_in_the_running_core(tmp_path):
    write_state(tmp_path, tot_duration=0)
    app = build_core(env_for(tmp_path))
    assert app.service.saved_config.tot_duration == RepeaterConfig().tot_duration
    assert len(app.config_notes) == 1


def test_building_does_not_open_a_device_or_import_the_web_app(tmp_path):
    write_state(tmp_path)
    app = build_core(env_for(tmp_path))
    assert app.runtime.live_audio.engine is None  # audio off in this config: nothing opened


def test_the_core_runs_with_the_network_down_and_stops_cleanly(tmp_path, monkeypatch):
    write_state(tmp_path)

    def no_network(*args, **kwargs):
        raise OSError("network is down")

    monkeypatch.setattr(socket, "getaddrinfo", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    app = build_core(env_for(tmp_path))

    async def scenario():
        stop = asyncio.Event()
        task = asyncio.create_task(serve(app, stop))
        for _ in range(100):
            await asyncio.sleep(0.02)
            if app.runtime.last_tick[0] is not None and app.runtime.last_tick[0] > 0:
                break
        started = app.runtime.last_tick[0]
        assert started is not None, "the controller tick did not start"
        await asyncio.sleep(0.3)
        assert app.runtime.last_tick[0] > started  # the tick keeps running
        stop.set()
        await asyncio.wait_for(task, 10)

    asyncio.run(scenario())


def test_main_exits_with_code_2_and_a_clear_message_when_it_cannot_start(tmp_path, capsys):
    assert main(env_for(tmp_path)) == EXIT_CONFIG
    assert "no saved configuration" in capsys.readouterr().err


def test_main_exits_2_for_a_blank_callsign(tmp_path, capsys):
    write_state(tmp_path, callsign="")
    assert main(env_for(tmp_path)) == EXIT_CONFIG
    assert "no callsign" in capsys.readouterr().err


# --- the unit file ----------------------------------------------------------------

def unit_text():
    return (ROOT / "packaging" / "moreopenrepeater-core.service").read_text()


def test_the_core_unit_is_off_by_default_and_never_runs_with_the_normal_service():
    unit = unit_text()
    assert "Conflicts=moreopenrepeater.service" in unit
    assert "[Install]" in unit
    assert "moreopenrepeater-core" not in (ROOT / "scripts" / "install-pi.sh").read_text()
    assert "moreopenrepeater-core" not in (ROOT / "scripts" / "update.sh").read_text()


def test_the_core_unit_starts_the_console_script_and_does_not_restart_on_a_config_exit():
    unit = unit_text()
    script = re.search(r'^moreopenrepeater-core = "([\w.]+):(\w+)"', (ROOT / "pyproject.toml").read_text(), re.M)
    assert script and script.groups() == ("core.main", "main")
    assert "ExecStart=/opt/moreopenrepeater/.venv/bin/moreopenrepeater-core" in unit
    assert f"RestartPreventExitStatus={EXIT_CONFIG}" in unit
    assert "Type=notify" in unit and "WatchdogSec=30" in unit


def test_the_core_unit_does_not_wait_for_the_network():
    unit = unit_text()
    assert not re.search(r"^(After|Wants|Requires)=.*network", unit, re.M)
