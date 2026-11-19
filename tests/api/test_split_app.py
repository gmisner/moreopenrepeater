"""The dashboard's API running apart from the core (Stage 3 slice 7c-7, #229): a real FastAPI app,
a real `RemoteCore`, and a real control server on a core built without devices."""
import asyncio
import json
import tempfile
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.assets import AudioAssetStore
from api.remote_core import RemoteCore
from core.build import build_core
from core.control import ControlServer


class CoreThread:
    """A core's control server on its own event loop, so the app under test can talk to it."""

    def __init__(self, core, path, **kwargs):
        self.core, self.path, self.kwargs = core, path, kwargs
        self.loop = asyncio.new_event_loop()
        self.server = None
        self._ready = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        asyncio.set_event_loop(self.loop)

        async def start():
            self.server = ControlServer(self.path, service=self.core.service, runtime=self.core.runtime, **self.kwargs)
            await self.server.start()
            self.core.runtime.last_tick[0] = time.monotonic()
            self._ready.set()

        self.loop.run_until_complete(start())
        self.loop.run_forever()

    def __enter__(self):
        self.thread.start()
        assert self._ready.wait(5)
        return self

    def __exit__(self, *exc):
        fut = asyncio.run_coroutine_threadsafe(self.server.stop(), self.loop)
        fut.result(5)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)


@pytest.fixture
def sock_dir():
    import shutil

    path = Path(tempfile.mkdtemp(prefix="mor-", dir="/tmp"))
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def core(tmp_path):
    (tmp_path / "state.json").write_text(json.dumps({"config": {"callsign": "W1AW", "audio_enabled": False}}))
    return build_core({"MOREOPENREPEATER_DATA_DIR": str(tmp_path), "MOREOPENREPEATER_LOG_PATH": str(tmp_path / "c.log")})


def split_client(path, **overrides):
    tmp = Path(tempfile.mkdtemp())
    remote = RemoteCore(path)
    app = create_app(
        service=remote, assets_store=AudioAssetStore(tmp / "audio"), log_path=tmp / "api.log", **overrides,
    )
    return TestClient(app), remote


def wait_for(condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.02)


def test_the_app_starts_with_no_core_and_says_so(sock_dir):
    client, remote = split_client(sock_dir / "nothing.sock")
    with client:
        status = client.get("/api/status")
        assert status.status_code == 200
        assert status.json()["state"] == "core_unavailable"


def test_the_app_follows_a_real_core(core, sock_dir):
    with CoreThread(core, sock_dir / "core.sock"):
        client, remote = split_client(sock_dir / "core.sock")
        with client:
            wait_for(lambda: remote.connected and remote._config is not None)
            status = client.get("/api/status").json()
            assert status["state"] == "idle" and status["callsign"] == "W1AW"


# -- the same app, many routes ---------------------------------------------------------------------


@pytest.fixture
def split(core, sock_dir):
    with CoreThread(core, sock_dir / "core.sock", allow_simulate=False, log_hub=core.log_hub) as thread:
        client, remote = split_client(sock_dir / "core.sock")
        with client:
            wait_for(lambda: remote.connected and remote._config is not None and remote._macros is not None)
            yield client, remote, core, thread


READS = [
    "/api/status", "/api/config", "/api/macros", "/api/announcements", "/api/net", "/api/links", "/api/links/events",
    "/api/snapshot", "/api/assets", "/api/recordings", "/api/mailbox", "/api/logs", "/api/audit", "/api/alerts",
    "/api/weather", "/api/boards", "/api/audio/openings", "/api/audio/glitches",
]


@pytest.mark.parametrize("path", READS)
def test_the_reads_work_against_a_core_in_another_process(split, path):
    client, *_ = split
    response = client.get(path)
    assert response.status_code == 200, (path, response.text[:200])


HARDWARE_READS = [
    "/api/gpio", "/api/link-radio", "/api/monitor-receiver", "/api/autopatch", "/api/allstar", "/api/dmr",
    "/api/audio/engine",
]


@pytest.mark.parametrize("path", HARDWARE_READS)
def test_the_pages_about_the_core_s_hardware_show_what_the_core_reports(split, path):
    client, remote, core, _ = split
    wait_for(lambda: remote._hardware_at is not None)
    response = client.get(path)
    assert response.status_code == 200, (path, response.text[:200])


def test_what_the_pages_show_is_the_core_s_own_status(split):
    client, remote, core, _ = split
    wait_for(lambda: remote._hardware_at is not None)
    assert client.get("/api/gpio").json()["available"] == core.runtime.gpio.status()["available"]
    assert client.get("/api/audio/engine").json()["enabled"] == core.runtime.live_audio.status()["enabled"]
    status = client.get("/api/status").json()
    assert status["gpio"] is not None and status["link_radio"] is not None  # the panels come back


@pytest.mark.parametrize("path", HARDWARE_READS)
def test_with_the_core_away_a_hardware_page_says_so_rather_than_showing_an_old_state(split, path):
    client, remote, core, thread = split
    wait_for(lambda: remote._hardware_at is not None)
    asyncio.run_coroutine_threadsafe(thread.server.stop(), thread.loop).result(5)
    wait_for(lambda: not remote.connected)
    response = client.get(path)
    assert response.status_code == 503 and response.json()["code"] == "core.unavailable"
    status = client.get("/api/status")
    assert status.status_code == 200 and status.json().get("gpio") is None  # the panel is left out, not invented
    assert client.get("/metrics").status_code == 200  # and metrics still answer


def test_acting_on_the_hardware_goes_through_the_core_and_its_refusals_come_back(split, monkeypatch):
    client, remote, core, _ = split
    wait_for(lambda: remote._hardware_at is not None)
    # The core has no CM108 here, so it refuses in its own words, and they reach the dashboard.
    refused = client.put("/api/gpio/1", json={"on": True})
    assert refused.status_code == 409 and "CM108" in refused.json()["detail"]
    dial = client.post("/api/autopatch/dial", json={"number": "5551234"})
    assert dial.status_code == 409 and "autopatch" in dial.json()["detail"].lower()  # "Autopatch is turned off."
    # With the core able to do it, it does it.
    calls = []
    monkeypatch.setattr(core.runtime.gpio, "set_output", lambda pin, on: calls.append(("gpio", pin, on)))
    monkeypatch.setattr(core.runtime.remote_base, "retune", lambda: calls.append(("retune",)))
    monkeypatch.setattr(core.runtime.autopatch, "hangup", lambda reason: calls.append(("hangup", reason)))
    assert client.put("/api/gpio/3", json={"on": True}).status_code == 200
    assert client.post("/api/link-radio/tune").status_code == 200
    assert client.post("/api/autopatch/hangup").status_code == 200
    assert calls[0] == ("gpio", 3, True) and calls[1] == ("retune",)
    assert calls[2][0] == "hangup" and "dashboard" in calls[2][1]


def test_a_call_is_placed_by_the_core_with_the_dashboard_user_as_the_actor(split, monkeypatch):
    client, remote, core, _ = split
    wait_for(lambda: remote._hardware_at is not None)
    dialled = []
    monkeypatch.setattr(core.runtime.autopatch, "dial", lambda number, actor: dialled.append((number, actor)) or None)
    response = client.post("/api/autopatch/dial", json={"number": "5551234"})
    assert response.status_code == 200, response.text
    assert dialled == [("5551234", "local")]


def test_with_the_core_away_a_hardware_command_says_nothing_was_done(split):
    client, remote, core, thread = split
    wait_for(lambda: remote._hardware_at is not None)
    asyncio.run_coroutine_threadsafe(thread.server.stop(), thread.loop).result(5)
    wait_for(lambda: not remote.connected)
    response = client.post("/api/autopatch/hangup")
    assert response.status_code == 503 and response.json()["code"] == "core.unavailable"


def test_the_trunk_page_is_the_apis_own_and_works_with_no_asterisk_configured(split):
    """The SIP trunk is set up over AMI, a network call, so the API owns it. With no AMI settings
    the page loads and says so, rather than answering that it needs the core's hardware."""
    client, *_ = split
    response = client.get("/api/autopatch/trunk")
    assert response.status_code == 200, response.text
    assert response.json()["configured"] is False and response.json()["error"] is None


@pytest.fixture
def split_with_asterisk(core, sock_dir):
    from test_sip_trunk import SETTINGS, FakeAsterisk

    from api.sip_trunk import SipTrunk

    asterisk = FakeAsterisk()
    with CoreThread(core, sock_dir / "core.sock", allow_simulate=False, log_hub=core.log_hub):
        client, remote = split_client(sock_dir / "core.sock", sip_trunk=SipTrunk(SETTINGS, asterisk.client))
        with client:
            wait_for(lambda: remote.connected and remote._config is not None and remote._hardware_at is not None)
            yield client, remote, core, asterisk


def test_the_trunk_is_set_up_from_the_dashboard_and_the_core_takes_the_new_settings(split_with_asterisk):
    from api.sip_trunk import DIAL_STRING

    client, remote, core, asterisk = split_with_asterisk
    body = {"server": "sip.telnyx.com", "username": "alice", "password": "s3cret;x", "ten_digit_prefix": "+1"}
    assert client.get("/api/autopatch/trunk").json()["configured"] is True
    assert client.post("/api/autopatch/trunk/modules").json()["missing_modules"] == []
    response = client.put("/api/autopatch/trunk", json=body)
    assert response.status_code == 200, response.text  # no call is running: it must not claim one is
    assert response.json()["in_use"] is True and "s3cret" not in response.text
    # The configuration belongs to the core, so that is where the change landed.
    assert core.service.config.autopatch_dial_string == DIAL_STRING
    assert core.service.config.autopatch_ten_digit_prefix == "+1"
    assert client.delete("/api/autopatch/trunk").json()["trunk"] is None


def test_a_call_in_progress_still_blocks_a_trunk_change(split_with_asterisk, monkeypatch):
    client, remote, core, asterisk = split_with_asterisk
    real = core.runtime.autopatch.status
    monkeypatch.setattr(core.runtime.autopatch, "status", lambda: {**real(), "call": {"number": "5551234"}})
    wait_for(lambda: (remote.hardware_status("autopatch") or {}).get("call"), timeout=8.0)
    body = {"server": "sip.telnyx.com", "username": "alice", "password": "x", "ten_digit_prefix": "+1"}
    response = client.put("/api/autopatch/trunk", json=body)
    assert response.status_code == 409 and "Hang up" in response.json()["detail"]
    assert client.delete("/api/autopatch/trunk").status_code == 409


def test_the_status_is_the_core_s_and_the_config_is_the_core_s(split):
    client, remote, core, _ = split
    status = client.get("/api/status").json()
    assert status["callsign"] == "W1AW" and status["state"] == "idle" and status["transmitter_enabled"] is True
    config = client.get("/api/config").json()
    assert config["callsign"] == "W1AW"
    core.service.update_config(tot_duration=77.0)
    wait_for(lambda: client.get("/api/config").json()["tot_duration"] == 77.0)


def test_changing_the_config_goes_through_the_core(split):
    client, remote, core, _ = split
    response = client.put("/api/config", json={"tot_duration": 123})
    assert response.status_code == 200 and response.json()["tot_duration"] == 123
    assert core.service.saved_config.tot_duration == 123  # the core has it
    assert json.loads(core.state_path.read_text())["config"]["tot_duration"] == 123  # and saved it


def test_the_core_s_refusal_reaches_the_dashboard(split):
    client, remote, core, _ = split
    before = core.service.saved_config
    response = client.put("/api/config", json={"callsign": "N0CALL", "hang_time": -5})
    assert response.status_code in (422, 400)  # the API's own check or the core's
    assert core.service.saved_config == before


def test_macros_and_announcements_are_the_core_s(split):
    client, remote, core, _ = split
    added = client.post("/api/macros", json={"pattern": "*71", "description": "net on", "action": "net_start"})
    assert added.status_code == 200 and [m["pattern"] for m in added.json()] == ["*71"]
    assert [m.pattern for m in core.service.list_macros()] == ["*71"]
    created = client.post("/api/announcements", json={"name": "Hourly", "message": "W1AW repeater", "every_minutes": 60})
    assert created.status_code == 200 and created.json()["next_run"] is not None
    assert len(core.service.list_announcements()) == 1
    assert client.delete("/api/macros/*71").status_code == 200 and core.service.list_macros() == []
    announcement_id = created.json()["id"]
    assert client.delete(f"/api/announcements/{announcement_id}").status_code == 200
    assert core.service.list_announcements() == []


def test_a_net_starts_and_ends_through_the_core(split):
    client, remote, core, _ = split
    assert client.post("/api/net/start", json={"name": "Test net"}).status_code == 200
    wait_for(lambda: core.service.net_active)
    wait_for(lambda: client.get("/api/status").json()["net_active"] is True)  # and the dashboard sees it
    assert client.post("/api/net/end").status_code == 200
    wait_for(lambda: not core.service.net_active)


def test_the_backup_export_comes_from_the_core(split):
    client, remote, core, _ = split
    core.service.update_config(tot_duration=88.0)
    wait_for(lambda: client.get("/api/snapshot").json()["config"]["tot_duration"] == 88.0)


def test_the_simulator_is_refused_by_a_core_on_a_real_radio(split):
    client, remote, core, _ = split
    response = client.post("/api/simulate/cos", json={"active": True})
    assert response.status_code == 403 and response.json()["code"] == "command.simulation_disabled"
    assert core.service.cos_active is False


def test_writes_say_so_when_the_core_goes_away(split):
    client, remote, core, thread = split
    asyncio.run_coroutine_threadsafe(thread.server.stop(), thread.loop).result(5)
    response = client.put("/api/config", json={"tot_duration": 99})
    assert response.status_code == 503 and response.json()["code"] == "core.unavailable"
    assert "nothing was changed" in response.json()["detail"]


def test_a_dashboard_can_watch_the_core_over_the_websocket(split):
    client, remote, core, _ = split
    with client.websocket_connect("/ws/status") as ws:
        first = ws.receive_json()
        assert first["callsign"] == "W1AW" and first["state"] == "idle"


def test_the_environment_chooses_the_remote_core():
    import importlib

    app_module = importlib.import_module("api.app")
    assert app_module._remote_core_from_env({}) is None
    assert app_module._remote_core_from_env({"MOREOPENREPEATER_CORE_SOCKET": "  "}) is None
    remote = app_module._remote_core_from_env({"MOREOPENREPEATER_CORE_SOCKET": "/run/moreopenrepeater/core.sock"})
    assert isinstance(remote, RemoteCore) and str(remote.socket_path) == "/run/moreopenrepeater/core.sock"


def test_the_dashboard_never_shows_a_state_for_a_core_it_cannot_hear(split):
    client, remote, core, thread = split
    assert client.get("/api/status").json()["state"] == "idle"
    asyncio.run_coroutine_threadsafe(thread.server.stop(), thread.loop).result(5)
    wait_for(lambda: not remote.connected)
    status = client.get("/api/status").json()
    assert status["state"] == "core_unavailable" and status["ptt_active"] is False and status["callsign"] == "W1AW"


def test_the_logs_page_shows_the_core_s_lines_with_the_api_s(split):
    client, remote, core, _ = split
    core.log_hub.publish("2026-01-01 00:00:00 INFO moreopenrepeater.controller: state idle -> transmitting_id", "INFO")
    wait_for(lambda: any("transmitting_id" in line["line"] for line in client.get("/api/logs/since?after=0").json()["lines"]))
    lines = [line["line"] for line in client.get("/api/logs/since?after=0").json()["lines"]]
    assert any("remote_core" in line for line in lines)  # the API's own line is there too


def test_starting_before_the_core_is_up_logs_no_errors_and_the_loops_carry_on(sock_dir, caplog):
    import logging

    caplog.set_level(logging.WARNING)
    client, remote = split_client(sock_dir / "nothing.sock")
    with client:
        time.sleep(2.5)  # long enough for every background loop to have run once with no core to read
        assert client.get("/api/status").json()["state"] == "core_unavailable"
    bad = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert bad == [], bad
