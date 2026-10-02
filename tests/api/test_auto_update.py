import tempfile
from datetime import datetime
from pathlib import Path

from fastapi.testclient import TestClient

from api.app import create_app
from api.assets import AudioAssetStore
from api.auto_update import CHECK_EVERY_SECONDS, AutoUpdater, in_window, next_window
from api.persistence import StateStore
from api.service import RepeaterService
from controller.state_machine import RepeaterConfig

NIGHT = {"enabled": True, "days": [0, 1, 2, 3, 4, 5, 6], "start": "02:00", "end": "05:00", "idle_minutes": 15}


def test_windows():
    thursday = datetime(2026, 10, 1)
    assert in_window(NIGHT, thursday.replace(hour=3))
    assert not in_window(NIGHT, thursday.replace(hour=5))
    assert not in_window({**NIGHT, "days": [0]}, thursday.replace(hour=3))

    overnight = {**NIGHT, "start": "23:00", "end": "01:00", "days": [3]}  # Thursday night
    assert in_window(overnight, thursday.replace(hour=23, minute=30))
    assert in_window(overnight, datetime(2026, 10, 2, 0, 30))  # still Thursday's window
    assert not in_window(overnight, thursday.replace(hour=0, minute=30))  # Wednesday's, which isn't on

    assert next_window(NIGHT, thursday.replace(hour=12)) == datetime(2026, 10, 2, 2, 0)
    assert next_window({**NIGHT, "days": [0]}, thursday) == datetime(2026, 10, 5, 2, 0)


class FakeUpdater:
    available = True

    def __init__(self):
        self.requests = []
        self.last = None

    def status(self):
        return self.last

    def busy(self):
        return bool(self.last) and self.last["state"] in ("requested", "running")

    def request(self, channel):
        self.requests.append(channel)


class FakeChecker:
    def __init__(self, relation="ahead", sha="b" * 40):
        self.relation, self.sha, self.calls = relation, sha, 0

    def check(self, channel, current_sha, *, refresh=False):
        self.calls += 1
        return {"relation": self.relation, "latest": {"sha": self.sha}, "error": None}


def make(idle=3600.0, hour=3, **settings):
    clock = [1_000_000.0]
    wall = [datetime(2026, 10, 1, hour)]
    updater, checker = FakeUpdater(), FakeChecker()
    auto = AutoUpdater(
        None, updater, checker, lambda: "stable", lambda: "a" * 40, lambda: idle,
        clock=lambda: clock[0], wall_clock=lambda: wall[0],
    )
    auto.update_settings(**{**NIGHT, **settings})
    return auto, updater, checker, clock


def test_installs_a_newer_version_when_quiet_in_the_window():
    audited = []
    auto, updater, checker, clock = make()
    auto.audit_hook = lambda *entry: audited.append(entry)
    auto.tick()
    assert updater.requests == ["stable"]
    assert audited == [("automatic update", "Update", "stable bbbbbbb")]
    assert auto.status()["last_result"] == "Installing bbbbbbb from stable."

    updater.last = {"state": "running", "started_at": clock[0]}
    clock[0] += CHECK_EVERY_SECONDS
    auto.tick()
    assert updater.requests == ["stable"]  # busy


def test_waits_for_the_window_quiet_and_a_newer_version():
    auto, updater, checker, _ = make(enabled=False)
    auto.tick()
    auto, updater, checker, _ = make(hour=12)
    auto.tick()
    assert checker.calls == 0

    auto, updater, checker, _ = make(idle=60)
    auto.tick()
    assert checker.calls == 0  # no GitHub call while it's in use
    assert auto.status()["last_result"] == "Waiting for the repeater to be quiet."

    auto, updater, checker, clock = make()
    checker.relation = "identical"
    auto.tick()
    auto.tick()  # checks at most every CHECK_EVERY_SECONDS
    assert checker.calls == 1 and updater.requests == []
    assert auto.status()["last_result"] == "Up to date."


def test_a_rolled_back_version_isnt_tried_again():
    auto, updater, checker, clock = make()
    auto.tick()
    updater.last = {"state": "rolled_back", "to_sha": "b" * 40, "started_at": clock[0]}
    for _ in range(3):
        clock[0] += 24 * 3600
        auto.tick()
    assert updater.requests == ["stable"]
    assert "rolled back" in auto.status()["last_result"]

    checker.sha = "c" * 40  # a fix
    clock[0] += CHECK_EVERY_SECONDS
    auto.tick()
    assert updater.requests == ["stable", "stable"]


def test_a_failed_try_waits_for_the_next_night():
    auto, updater, checker, clock = make()
    auto.tick()
    updater.last = {"state": "failed", "started_at": clock[0]}
    clock[0] += CHECK_EVERY_SECONDS
    auto.tick()
    assert len(updater.requests) == 1
    clock[0] += 24 * 3600
    auto.tick()
    assert len(updater.requests) == 2


def test_settings_survive_a_restart():
    path = Path(tempfile.mkdtemp()) / "auto-update.json"
    auto = AutoUpdater(StateStore(path), FakeUpdater(), FakeChecker(), lambda: "stable", lambda: "", lambda: 0.0)
    auto.update_settings(enabled=True, start="01:00")
    again = AutoUpdater(StateStore(path), FakeUpdater(), FakeChecker(), lambda: "stable", lambda: "", lambda: 0.0)
    assert again.settings["enabled"] is True and again.settings["start"] == "01:00"


def test_idle_seconds():
    now = [0.0]
    service = RepeaterService(config=RepeaterConfig(), clock=lambda: now[0])
    now[0] = 100.0
    assert service.idle_seconds() == 100.0
    service.simulate_remote_keyed("2000", True)
    assert service.idle_seconds() == 0.0
    service.set_net_active(True)
    service.simulate_remote_keyed("2000", False)
    assert service.idle_seconds() == 0.0


def test_auto_update_api():
    tmp = Path(tempfile.mkdtemp())
    client = TestClient(create_app(
        service=RepeaterService(config=RepeaterConfig()), start_background_tick=False,
        assets_store=AudioAssetStore(tmp / "audio"), log_path=tmp / "test.log",
    ))
    assert client.get("/api/updates").json()["auto"]["enabled"] is False
    auto = client.put("/api/updates/auto", json={**NIGHT, "start": "1:30", "days": [6, 0, 0]}).json()["auto"]
    assert auto["start"] == "01:30" and auto["days"] == [0, 6] and auto["next_window"]
    assert client.put("/api/updates/auto", json={**NIGHT, "end": "02:00"}).status_code == 422
    assert client.put("/api/updates/auto", json={**NIGHT, "idle_minutes": 0}).status_code == 422
