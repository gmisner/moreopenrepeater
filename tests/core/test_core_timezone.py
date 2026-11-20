"""The station's time zone in the core: checked at the core's own boundary, applied at start and on every change.
The core is its own process in a container, so the proof that it follows on its own is a real separate interpreter."""
import asyncio
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from control_helpers import make_server
from test_control_config import make_change, post_config

from controller import station_clock
from controller.config import RepeaterConfig
from core.build import build_core
from core.config_boundary import ConfigRejected, check_change, check_config
from contracts import ConfigApplied, ConfigResult

SRC = str(Path(__file__).resolve().parents[2] / "src")


def write_state(root: Path, **config) -> None:
    (root / "state.json").write_text(json.dumps({"config": {"callsign": "W1AW", "audio_enabled": False, **config}}))


def env_for(root: Path) -> dict:
    return {"MOREOPENREPEATER_DATA_DIR": str(root), "MOREOPENREPEATER_LOG_PATH": str(root / "core.log")}


# -- the boundary -------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("zone", ["America/Phoenix", "Asia/Kolkata", ""])
def test_the_core_accepts_a_zone_and_the_empty_setting(zone):
    assert check_change(RepeaterConfig(callsign="W1AW"), {"timezone": zone}).timezone == zone


@pytest.mark.parametrize("zone", ["Mars/Base", "../../etc/passwd", "Qx7", "Etc/../UTC"])
def test_the_core_refuses_a_zone_it_does_not_know_and_says_which_setting(zone):
    with pytest.raises(ConfigRejected) as refused:
        check_change(RepeaterConfig(callsign="W1AW"), {"timezone": zone})
    assert refused.value.code == "config.invalid_timezone"
    assert "timezone" in refused.value.message and zone not in refused.value.message  # names the setting, never the value


def test_the_core_refuses_a_zone_that_is_not_text():
    with pytest.raises(ConfigRejected) as refused:
        check_change(RepeaterConfig(callsign="W1AW"), {"timezone": 7})
    assert refused.value.code == "config.wrong_type"


def test_a_saved_zone_the_system_does_not_know_is_replaced_and_the_core_keeps_going():
    config, notes = check_config(RepeaterConfig(callsign="W1AW", timezone="Mars/Base"), log=False)
    assert config.timezone == "" and len(notes) == 1 and notes[0].startswith("timezone ")


def test_a_good_saved_zone_is_left_alone():
    config, notes = check_config(RepeaterConfig(callsign="W1AW", timezone="Asia/Kolkata"), log=False)
    assert config.timezone == "Asia/Kolkata" and notes == []


def test_a_bad_saved_zone_does_not_stop_the_core_from_building(tmp_path):
    write_state(tmp_path, timezone="Mars/Base")
    app = build_core(env_for(tmp_path))
    assert app.service.saved_config.timezone == "" and station_clock.applied_timezone() == ""


# -- applied at start and on every change (in this process) ---------------------------------------------------------------


def test_the_core_starts_on_the_saved_zone(tmp_path):
    write_state(tmp_path, timezone="Asia/Kolkata")
    build_core(env_for(tmp_path))
    assert datetime.fromtimestamp(0).astimezone().utcoffset() == timedelta(hours=5, minutes=30)


def test_a_change_over_the_control_socket_moves_the_core_s_clock(core, sock_dir):
    async def go():
        server = make_server(core, sock_dir / "core.sock")
        await server.start()
        try:
            return await post_config(sock_dir / "core.sock", make_change({"timezone": "Asia/Kolkata"}))
        finally:
            await server.stop()

    status, body = asyncio.run(go())
    assert status == 200 and ConfigApplied.model_validate(body).result is ConfigResult.APPLIED
    assert core.service.saved_config.timezone == "Asia/Kolkata"
    assert datetime.fromtimestamp(0).astimezone().utcoffset() == timedelta(hours=5, minutes=30)


def test_a_refused_zone_over_the_control_socket_changes_nothing(core, sock_dir):
    async def go():
        server = make_server(core, sock_dir / "core.sock")
        await server.start()
        try:
            return await post_config(sock_dir / "core.sock", make_change({"timezone": "Mars/Base"}))
        finally:
            await server.stop()

    status, body = asyncio.run(go())
    assert ConfigApplied.model_validate(body).result is ConfigResult.REJECTED
    assert core.service.saved_config.timezone == "" and station_clock.applied_timezone() == ""


# -- the core is its own process --------------------------------------------------------------------------------------------

CORE_SCRIPT = """
import sys, json
sys.path.insert(0, sys.argv[1])
from datetime import datetime
from core.build import build_core
import os

def hour_at_noon_utc():
    return datetime.fromtimestamp(1795262400).hour   # 2026-11-20 12:00 UTC

app = build_core(dict(os.environ))
seen = [hour_at_noon_utc()]
app.service.update_config(timezone="America/Phoenix")   # what a change from the dashboard does in the core
seen.append(hour_at_noon_utc())
app.service.update_config(timezone="")
seen.append(hour_at_noon_utc())
print(json.dumps(seen))
"""


def run_core_process(root: Path, *, tz: str):
    env = {"PATH": os.environ["PATH"], "TZ": tz, **env_for(root)}
    result = subprocess.run(
        [sys.executable, "-I", "-c", CORE_SCRIPT, SRC], env=env, capture_output=True, text=True, timeout=120, check=False
    )
    assert result.returncode == 0, result.stderr[-2000:]
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_a_separate_core_process_starts_on_the_saved_zone_follows_a_change_and_goes_back_to_its_own(tmp_path):
    write_state(tmp_path, timezone="Asia/Kolkata")
    # TZ=UTC is the container's own. Noon UTC is 17:30 in Kolkata (hour 17), 05:00 in Phoenix, 12 in UTC.
    assert run_core_process(tmp_path, tz="UTC") == [17, 5, 12]


def test_a_separate_core_process_with_no_saved_zone_stays_on_the_container_s(tmp_path):
    write_state(tmp_path)
    assert run_core_process(tmp_path, tz="Asia/Tokyo") == [21, 5, 21]
