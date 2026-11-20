"""The station's time zone in the dashboard's API: validated when it is saved, applied to this process, and followed by
the core in its own process. Nothing here touches a radio or a Pi."""
import asyncio
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from test_split_app import CoreThread, split_client, wait_for
from test_split_app import core as _core
from test_split_app import sock_dir as _sock_dir

from api.app import create_app
from api.assets import AudioAssetStore
from api.link_schedule import LinkScheduler
from api.service import RepeaterService
from controller import station_clock
from controller.macros import Macro
from controller.state_machine import RepeaterConfig
from playout.tts import spoken_time

core = _core  # the fixtures: a real core built without devices, and a short socket folder
sock_dir = _sock_dir

SRC = str(Path(__file__).resolve().parents[2] / "src")


def make_client(**config):
    service = RepeaterService(config=RepeaterConfig(callsign="W1AW", **config), clock=lambda: 0.0)
    tmp_dir = Path(tempfile.mkdtemp())
    app = create_app(service=service, start_background_tick=False, assets_store=AudioAssetStore(tmp_dir / "audio"), log_path=tmp_dir / "t.log")
    return TestClient(app), service


def offset_now() -> timedelta:
    return datetime.now().astimezone().utcoffset()


# -- validation at the boundary ---------------------------------------------------------------------------------------


def test_a_zone_is_saved_and_comes_back_in_the_config():
    client, service = make_client()
    response = client.put("/api/config", json={"timezone": "America/Phoenix"})
    assert response.status_code == 200 and response.json()["timezone"] == "America/Phoenix"
    assert client.get("/api/config").json()["timezone"] == "America/Phoenix"
    assert service.saved_config.timezone == "America/Phoenix"


def test_an_unknown_zone_is_refused_in_plain_words_and_nothing_is_saved():
    client, service = make_client(timezone="America/Phoenix")
    response = client.put("/api/config", json={"timezone": "Mars/Base"})
    assert response.status_code == 422
    message = json.dumps(response.json())
    assert "not a time zone this station knows" in message and "America/Phoenix" in message
    assert service.saved_config.timezone == "America/Phoenix" and station_clock.applied_timezone() == "America/Phoenix"


@pytest.mark.parametrize("name", ["../../etc/passwd", "/etc/localtime", "America", "x" * 200, "america/phoenix"])
def test_names_that_are_not_zones_are_refused(name):
    client, _ = make_client()
    assert client.put("/api/config", json={"timezone": name}).status_code == 422


def test_empty_clears_the_setting_back_to_the_container_s_zone():
    client, service = make_client(timezone="Asia/Kolkata")
    assert client.put("/api/config", json={"timezone": ""}).status_code == 200
    assert service.saved_config.timezone == "" and station_clock.applied_timezone() == ""


def test_the_default_for_a_new_station_is_the_container_s_own_zone():
    assert RepeaterConfig().timezone == ""


# -- applied to this process ---------------------------------------------------------------------------------------------


def test_saving_a_zone_moves_the_dashboard_process_s_clock():
    client, _ = make_client()
    client.put("/api/config", json={"timezone": "Asia/Kolkata"})
    assert offset_now() == timedelta(hours=5, minutes=30)
    client.put("/api/config", json={"timezone": "America/Phoenix"})
    assert offset_now() == timedelta(hours=-7)


def test_the_app_starts_on_the_saved_zone():
    make_client(timezone="Asia/Kolkata")
    assert offset_now() == timedelta(hours=5, minutes=30)


def test_the_time_macro_says_the_hour_in_the_station_zone():
    service = RepeaterService(config=RepeaterConfig(callsign="W1AW", hang_time=1.0), macros=[Macro("*1", "time", action="time")], clock=lambda: 0.0)
    station_clock.follow(service.add_config_listener, service.saved_config)  # what the core and the app do at start
    service.update_config(timezone="Asia/Kolkata")
    before = spoken_time(datetime.now(ZoneInfo("Asia/Kolkata")))
    for digit in "*1":
        service.simulate_dtmf(digit)
    after = spoken_time(datetime.now(ZoneInfo("Asia/Kolkata")))
    assert service.controller.queued_announcements[-1] in (f"tts:The time is {before}.", f"tts:The time is {after}.")


# -- the Date and time page's read ------------------------------------------------------------------------------------------


def test_the_page_is_told_the_setting_the_zone_in_force_and_where_the_default_came_from(monkeypatch):
    monkeypatch.setattr(station_clock, "_BASE_TZ", "America/Phoenix")
    client, _ = make_client()
    body = client.get("/api/timezone").json()
    assert body["setting"] == "" and body["default"] == "America/Phoenix" and body["default_source"] == "container"
    assert body["effective"] == "America/Phoenix"
    assert "America/Phoenix" in body["zones"] and "Asia/Kolkata" in body["zones"]
    assert not [name for name in body["zones"] if name.startswith(("US/", "Etc/"))]


def test_a_chosen_zone_is_the_one_in_force_and_the_clock_reads_in_it():
    client, _ = make_client()
    client.put("/api/config", json={"timezone": "Asia/Kolkata"})
    body = client.get("/api/timezone").json()
    assert body["setting"] == "Asia/Kolkata" and body["effective"] == "Asia/Kolkata"
    assert body["now"].endswith("+05:30")  # an offset, so the page can say what the station's clock reads


def test_the_page_read_needs_no_core_to_have_been_reached(sock_dir):
    client, _ = split_client(sock_dir / "nothing.sock")
    with client:
        assert client.get("/api/timezone").status_code == 200


# -- the dashboard is its own process ---------------------------------------------------------------------------------------

API_SCRIPT = """
import sys, json, tempfile
sys.path.insert(0, sys.argv[1])
from datetime import datetime
from pathlib import Path
from fastapi.testclient import TestClient
from api.app import create_app
from api.assets import AudioAssetStore
from api.service import RepeaterService
from controller.state_machine import RepeaterConfig

def hour_at_noon_utc():
    return datetime.fromtimestamp(1795262400).hour   # 2026-11-20 12:00 UTC

tmp = Path(tempfile.mkdtemp())
service = RepeaterService(config=RepeaterConfig(callsign="W1AW", timezone=sys.argv[2]), clock=lambda: 0.0)
app = create_app(service=service, start_background_tick=False, assets_store=AudioAssetStore(tmp / "audio"), log_path=tmp / "t.log")
client = TestClient(app)
seen = [hour_at_noon_utc()]
assert client.put("/api/config", json={"timezone": "America/Phoenix"}).status_code == 200
seen.append(hour_at_noon_utc())
assert client.put("/api/config", json={"timezone": ""}).status_code == 200
seen.append(hour_at_noon_utc())
print(json.dumps(seen))
"""


def run_api_process(saved: str, tz: str):
    env = {"PATH": os.environ["PATH"], "TZ": tz, "MOREOPENREPEATER_DATA_DIR": tempfile.mkdtemp(), "MOREOPENREPEATER_LOG_PATH": tempfile.mkdtemp() + "/api.log"}
    result = subprocess.run([sys.executable, "-I", "-c", API_SCRIPT, SRC, saved], env=env, capture_output=True, text=True, timeout=120, check=False)
    assert result.returncode == 0, result.stderr[-2000:]
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_a_separate_dashboard_process_starts_on_the_saved_zone_follows_a_change_and_goes_back_to_its_own():
    assert run_api_process("Asia/Kolkata", "UTC") == [17, 5, 12]


def test_a_separate_dashboard_process_with_no_saved_zone_stays_on_the_container_s():
    assert run_api_process("", "Asia/Tokyo") == [21, 5, 21]


# -- the real pair: the dashboard saves, the core applies, and each process moves its own clock -------------------------------


@pytest.fixture
def applied(monkeypatch):
    """Every zone either process hands to `apply_timezone`, in order (the call still goes through)."""
    names = []
    real = station_clock.apply_timezone

    def record(name):
        names.append(name)
        return real(name)

    monkeypatch.setattr(station_clock, "apply_timezone", record)
    return names


@pytest.fixture
def pair(applied, core, sock_dir):
    """A real core on a socket and a real dashboard app following it, built after `applied` is watching."""
    with CoreThread(core, sock_dir / "core.sock", allow_simulate=False, log_hub=core.log_hub, activity_store=core.activity_store):
        client, remote = split_client(sock_dir / "core.sock")
        with client:
            wait_for(lambda: remote.connected and remote._config is not None)
            yield client, remote, core


def test_a_change_from_the_dashboard_reaches_the_core_and_both_of_them_apply_it(pair, applied):
    client, remote, core = pair
    applied.clear()
    response = client.put("/api/config", json={"timezone": "Asia/Kolkata"})
    assert response.status_code == 200 and response.json()["timezone"] == "Asia/Kolkata"
    # The core applied it (its own listener) and the dashboard applied it when it read the core's configuration (its own).
    wait_for(lambda: applied.count("Asia/Kolkata") >= 2)
    assert core.service.saved_config.timezone == "Asia/Kolkata"
    assert json.loads((Path(core.state_path)).read_text())["config"]["timezone"] == "Asia/Kolkata"  # saved by the core
    assert remote.saved_config.timezone == "Asia/Kolkata"
    assert client.get("/api/timezone").json()["effective"] == "Asia/Kolkata"
    assert offset_now() == timedelta(hours=5, minutes=30)


def test_a_zone_the_core_refuses_is_refused_before_either_process_moves(pair, applied):
    client, remote, core = pair
    applied.clear()
    assert client.put("/api/config", json={"timezone": "Mars/Base"}).status_code == 422
    assert "Mars/Base" not in applied and core.service.saved_config.timezone == ""


# -- the schedules that hold a local hour follow ------------------------------------------------------------------------------


def make_weather_service():
    station_clock.apply_timezone("UTC")  # a known starting zone, whatever machine this runs on
    clock = {"wall": datetime(2026, 11, 20, 14, 0)}
    service = RepeaterService(config=RepeaterConfig(wx_schedule_enabled=True, wx_schedule_minutes=30), clock=lambda: 0.0, wall_clock=lambda: clock["wall"])
    return service, clock


def test_the_routine_weather_timer_starts_again_from_the_new_zone_s_now():
    service, clock = make_weather_service()
    assert service.next_weather_schedule_run() == datetime(2026, 11, 20, 14, 30)
    station_clock.apply_timezone("America/Phoenix")  # the wall clock now reads seven hours earlier
    clock["wall"] = datetime(2026, 11, 20, 7, 0)
    assert not service.weather_schedule_due()  # 14:30 by the old clock must not be "seven hours from now"
    assert service.next_weather_schedule_run() == datetime(2026, 11, 20, 7, 30)


def test_a_weather_alert_announced_before_a_change_is_not_repeated_early_or_held_back_after_it():
    service, clock = make_weather_service()
    service.weather.tracker._last_announced = {"a1": datetime(2026, 11, 20, 14, 0)}
    service.weather.last_checked = datetime(2026, 11, 20, 14, 0)
    station_clock.apply_timezone("America/Phoenix")  # seven hours earlier on the wall clock
    service.weather.next_schedule_run()  # any use of the weather support notices the move
    assert service.weather.tracker._last_announced == {"a1": datetime(2026, 11, 20, 7, 0)}  # the same moment, in the new zone's hours
    assert service.weather.last_checked == datetime(2026, 11, 20, 7, 0)


NET = {"node": "2000", "name": "Tuesday net", "days": [1], "time": "19:00", "minutes": 60, "monitor": False, "enabled": True}


class FakeLinks:
    def __init__(self):
        self.up = {}
        self.calls = []

    async def linked(self):
        return dict(self.up)

    async def connect(self, node, monitor):
        self.calls.append(("connect", node))
        self.up[node] = ("transceive", False)

    async def disconnect(self, node):
        self.calls.append(("disconnect", node))
        self.up.pop(node, None)


def test_a_link_the_schedule_made_is_still_dropped_at_the_same_moment_after_a_zone_change():
    station_clock.apply_timezone("UTC")
    links = FakeLinks()
    scheduler = LinkScheduler(links, lambda: [NET])
    tuesday = datetime(2026, 9, 29)  # a Tuesday
    asyncio.run(scheduler.tick(tuesday.replace(hour=19, minute=1)))
    assert links.calls == [("connect", "2000")] and scheduler.until("2000") == tuesday.replace(hour=20)
    station_clock.apply_timezone("America/Phoenix")  # 19:01 UTC is 12:01 in Phoenix; the window ends in 59 minutes
    asyncio.run(scheduler.tick(tuesday.replace(hour=12, minute=2)))
    assert scheduler.until("2000") == tuesday.replace(hour=13)
    assert links.calls == [("connect", "2000")]  # not dropped early, and not linked a second time
    asyncio.run(scheduler.tick(tuesday.replace(hour=13)))
    assert links.calls[-1] == ("disconnect", "2000")
