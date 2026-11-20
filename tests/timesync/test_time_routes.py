"""GET /api/time and PUT /api/time/ntp, the cached never-blocking check behind them, and the container's status file."""
import asyncio
import base64
import json
import os
import stat
import threading
import time

import pytest
from fastapi.testclient import TestClient

from api import time_sync as ts
from api.app import create_app
from api.auth import AuthSettings
from api.service import RepeaterService
from api.users import UserStore

ADMIN = AuthSettings(username="admin", password="hunter2hunter2")
FIELDS = {
    "server", "configured_server", "default_server", "state", "reachable", "offset_ms", "round_trip_ms", "stratum", "limit_ms",
    "checked_at", "error", "error_text", "container",
}


def basic(username="admin", password="hunter2hunter2"):
    return {"Authorization": "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()}


class Query:
    """A stand-in for the SNTP query that counts its calls and answers what it is told."""

    def __init__(self, result=None):
        self.result = result or ts.SntpResult(True, offset=0.012, delay=0.034, stratum=2)
        self.calls = []

    def __call__(self, server):
        self.calls.append(server)
        return self.result


def make(tmp_path, query=None, *, auth=True, wall=time.time, **check):
    config = tmp_path / "time-config"
    status = tmp_path / "time-status"
    setting = ts.NtpSetting(config)
    query = query or Query()
    sync = ts.TimeSync(setting, ts.ClockCheck(setting, query, wall=wall, **check), status, wall=wall)
    users = UserStore(reserved_username=ADMIN.username)
    if auth:
        users.add("op", "operator-pass", "operator")
    app = create_app(
        service=RepeaterService(), start_background_tick=False, log_path=tmp_path / "t.log",
        auth_settings=ADMIN if auth else None, users=users, time_sync=sync,
    )
    return TestClient(app), sync, query, config, status


# -- who may do what -----------------------------------------------------------------------------------------------------------


def test_reading_needs_a_login_like_the_other_status_routes(tmp_path):
    client, *_ = make(tmp_path)
    assert client.get("/api/time").status_code == 401
    assert client.get("/api/time", headers=basic()).status_code == 200
    assert client.get("/api/time", headers=basic("op", "operator-pass")).status_code == 200  # any signed-in account, as /api/winlink


def test_changing_the_server_is_for_admins_only(tmp_path):
    client, sync, _query, config, _ = make(tmp_path)
    assert client.put("/api/time/ntp", json={"server": "time.example.org"}).status_code == 401
    assert client.put("/api/time/ntp", json={"server": "time.example.org"}, headers=basic("op", "operator-pass")).status_code == 403
    assert not (config / "ntp.json").exists() and sync.setting.get() == ""  # neither attempt changed anything
    assert client.put("/api/time/ntp", json={"server": "time.example.org"}, headers=basic()).status_code == 200


def test_with_sign_in_off_the_dashboard_is_open_as_everywhere_else(tmp_path):
    client, *_ = make(tmp_path, auth=False)
    assert client.get("/api/time").status_code == 200
    assert client.put("/api/time/ntp", json={"server": "time.example.org"}).status_code == 200


# -- the status ---------------------------------------------------------------------------------------------------------------------


def test_a_good_check_says_ok_with_the_numbers(tmp_path):
    client, _sync, query, *_ = make(tmp_path)
    body = client.get("/api/time", headers=basic()).json()
    assert set(body) == FIELDS
    assert body["state"] == "ok" and body["reachable"] is True
    assert body["offset_ms"] == 12.0 and body["round_trip_ms"] == 34.0 and body["stratum"] == 2
    assert body["server"] == "pool.ntp.org" and body["configured_server"] == "" and body["default_server"] == "pool.ntp.org"
    assert body["limit_ms"] == 2000 and body["error"] is None and body["error_text"] is None
    assert abs(body["checked_at"] - time.time()) < 5
    assert query.calls == ["pool.ntp.org"]


@pytest.mark.parametrize("offset, state", [(1.999, "ok"), (-1.999, "ok"), (2.0, "off"), (-2.0, "off"), (3600.0, "off"), (-86400.0, "off")])
def test_off_by_the_limit_or_more_in_either_direction_is_off(tmp_path, offset, state):
    client, *_ = make(tmp_path, Query(ts.SntpResult(True, offset=offset, delay=0.01, stratum=3)))
    assert client.get("/api/time", headers=basic()).json()["state"] == state


@pytest.mark.parametrize("error", ["resolve", "timeout", "network", "kiss", "unsynchronized", "bad_reply"])
def test_a_check_with_no_reading_is_unreachable_and_says_why_in_words(tmp_path, error):
    client, *_ = make(tmp_path, Query(ts.SntpResult(False, error=error)))
    body = client.get("/api/time", headers=basic()).json()
    assert body["state"] == "unreachable" and body["reachable"] is False and body["error"] == error
    assert body["offset_ms"] is None and body["round_trip_ms"] is None and body["stratum"] is None
    assert body["error_text"] == ts.ERROR_TEXT[error] and "—" not in body["error_text"]


def test_a_switched_off_check_says_not_checked_and_never_touches_the_network(tmp_path):
    setting = ts.NtpSetting(None)
    sync = ts.TimeSync(setting, ts.ClockCheck(setting, None))
    app = create_app(service=RepeaterService(), start_background_tick=False, log_path=tmp_path / "t.log", time_sync=sync)
    body = TestClient(app).get("/api/time").json()
    assert body["state"] == "not_checked" and body["reachable"] is None and body["checked_at"] is None
    assert body["container"]["running"] is False


def test_an_app_built_without_real_settings_never_checks_the_network(tmp_path):
    app = create_app(service=RepeaterService(), start_background_tick=False, log_path=tmp_path / "t.log")
    assert TestClient(app).get("/api/time").json()["state"] == "not_checked"


# -- the cache ---------------------------------------------------------------------------------------------------------------------


def test_the_answer_is_cached_so_a_page_that_polls_asks_the_server_once(tmp_path):
    client, _sync, query, *_ = make(tmp_path)
    for _ in range(5):
        client.get("/api/time", headers=basic())
    assert len(query.calls) == 1


def test_the_answer_is_asked_for_again_after_the_cache_runs_out(tmp_path):
    now = [1000.0]
    client, _sync, query, *_ = make(tmp_path, clock=lambda: now[0])
    client.get("/api/time", headers=basic())
    now[0] += ts.CACHE_SECONDS - 1
    client.get("/api/time", headers=basic())
    assert len(query.calls) == 1
    now[0] += 2
    client.get("/api/time", headers=basic())
    assert len(query.calls) == 2


def test_the_cache_lasts_about_a_minute():
    assert 30 <= ts.CACHE_SECONDS <= 120


def test_callers_that_arrive_while_a_check_runs_share_it(tmp_path):
    release = threading.Event()
    calls = []

    def slow(server):
        calls.append(server)
        release.wait(5)
        return ts.SntpResult(True, offset=0.0, delay=0.01, stratum=2)

    setting = ts.NtpSetting(tmp_path)

    async def scenario():
        check = ts.ClockCheck(setting, slow)
        tasks = [asyncio.ensure_future(check.current()) for _ in range(4)]
        await asyncio.sleep(0.1)
        release.set()
        return await asyncio.gather(*tasks)

    readings = asyncio.run(scenario())
    assert len(calls) == 1 and all(r is readings[0] for r in readings)


def test_changing_the_server_throws_the_cache_away_and_checks_the_new_one(tmp_path):
    client, _sync, query, *_ = make(tmp_path)
    client.get("/api/time", headers=basic())
    response = client.put("/api/time/ntp", json={"server": "time.example.org"}, headers=basic())
    assert query.calls == ["pool.ntp.org", "time.example.org"]
    assert response.json()["server"] == "time.example.org" and response.json()["state"] == "ok"
    client.get("/api/time", headers=basic())
    assert len(query.calls) == 2  # and that answer is cached in turn


def test_an_answer_about_the_old_server_is_not_used_for_the_new_one(tmp_path):
    release = threading.Event()
    asked = []

    def slow(server):
        asked.append(server)
        release.wait(5)
        return ts.SntpResult(True, offset=9.0, delay=0.01, stratum=2)

    setting = ts.NtpSetting(tmp_path)

    async def scenario():
        check = ts.ClockCheck(setting, slow)
        first = asyncio.ensure_future(check.current())
        await asyncio.sleep(0.1)
        setting.set("other.example.org")  # the setting changes while the first check is out
        release.set()
        await first
        return check._cached

    assert asyncio.run(scenario()) is None and asked == ["pool.ntp.org"]


# -- never blocking --------------------------------------------------------------------------------------------------------------


def test_a_slow_check_does_not_stop_the_event_loop():
    setting = ts.NtpSetting(None)

    def slow(server):
        time.sleep(0.5)
        return ts.SntpResult(True, offset=0.0, delay=0.01, stratum=2)

    async def scenario():
        check = ts.ClockCheck(setting, slow)
        ticks = 0
        job = asyncio.ensure_future(check.current())
        while not job.done():
            await asyncio.sleep(0.01)
            ticks += 1
        return ticks

    assert asyncio.run(scenario()) > 20  # the loop kept turning for the half second the check took


def test_a_check_that_hangs_is_given_up_on_at_the_deadline_and_is_unreachable():
    setting = ts.NtpSetting(None)
    stop = threading.Event()

    def hangs(server):
        stop.wait(10)
        return ts.SntpResult(True, offset=0.0, delay=0.01, stratum=2)

    async def scenario():
        check = ts.ClockCheck(setting, hangs, deadline=0.3)
        started = time.monotonic()
        reading = await check.current()
        return reading, time.monotonic() - started

    try:
        reading, elapsed = asyncio.run(scenario())
    finally:
        stop.set()
    assert reading.state == "unreachable" and reading.result.error == "timeout" and elapsed < 2


def test_a_query_that_raises_is_unreachable_not_a_500(tmp_path):
    def broken(server):
        raise RuntimeError("boom")

    client, *_ = make(tmp_path, broken)
    response = client.get("/api/time", headers=basic())
    assert response.status_code == 200 and response.json()["state"] == "unreachable"


# -- the setting -------------------------------------------------------------------------------------------------------------------


def test_the_server_is_saved_where_the_container_reads_it(tmp_path):
    client, sync, _q, config, _ = make(tmp_path)
    response = client.put("/api/time/ntp", json={"server": "time.example.org"}, headers=basic())
    assert response.status_code == 200 and response.json()["configured_server"] == "time.example.org"
    path = config / "ntp.json"
    assert json.loads(path.read_text()) == {"server": "time.example.org"} and stat.S_IMODE(path.stat().st_mode) == 0o644
    assert client.get("/api/time", headers=basic()).json()["configured_server"] == "time.example.org"
    assert sync.setting.effective == "time.example.org"


def test_an_empty_server_goes_back_to_the_default(tmp_path):
    client, _sync, _q, config, _ = make(tmp_path)
    client.put("/api/time/ntp", json={"server": "time.example.org"}, headers=basic())
    body = client.put("/api/time/ntp", json={"server": ""}, headers=basic()).json()
    assert body["configured_server"] == "" and body["server"] == "pool.ntp.org"
    assert json.loads((config / "ntp.json").read_text()) == {"server": ""}
    assert client.put("/api/time/ntp", json={}, headers=basic()).json()["configured_server"] == ""  # no value is the same as empty


@pytest.mark.parametrize("bad", ["-pool.ntp.org", "a b", "a;b", "a\nserver x", "$(id)", "127.0.0.1", "bücher.example", "a" * 254, "1.2.3"])
def test_a_bad_value_is_a_400_with_a_sentence_and_changes_nothing(tmp_path, bad):
    client, sync, query, config, _ = make(tmp_path)
    client.put("/api/time/ntp", json={"server": "good.example"}, headers=basic())
    calls = len(query.calls)
    response = client.put("/api/time/ntp", json={"server": bad}, headers=basic())
    assert response.status_code in (400, 422)
    assert json.loads((config / "ntp.json").read_text()) == {"server": "good.example"}
    assert sync.setting.get() == "good.example" and len(query.calls) == calls  # nothing was checked against it either


def test_a_value_of_the_wrong_type_is_refused(tmp_path):
    client, *_ = make(tmp_path)
    for body in ({"server": 5}, {"server": ["a"]}, {"server": None}, [], "pool.ntp.org"):
        assert client.put("/api/time/ntp", json=body, headers=basic()).status_code == 422


def test_a_folder_that_cannot_be_written_is_said_plainly(tmp_path):
    client, sync, *_ = make(tmp_path)
    blocked = tmp_path / "file-not-folder"
    blocked.write_text("")
    sync.setting.path = blocked / "ntp.json"  # a parent that is a file: the write cannot happen
    response = client.put("/api/time/ntp", json={"server": "time.example.org"}, headers=basic())
    assert response.status_code == 500 and "not writable" in response.json()["detail"]


def test_the_audit_log_says_the_server_changed_but_not_to_what(tmp_path):
    client, *_ = make(tmp_path)
    client.put("/api/time/ntp", json={"server": "secret-time.example.org"}, headers=basic())
    entries = client.get("/api/audit", headers=basic()).json()
    put = [e for e in entries if e["action"] == "PUT /api/time/ntp"]
    assert len(put) == 1 and put[0]["detail"] == "NTP server changed" and put[0]["status"] == 200
    assert "secret-time" not in json.dumps(entries)


def test_the_log_says_the_server_changed_in_a_named_event_without_the_name(tmp_path, structured_events):
    client, *_ = make(tmp_path)
    client.put("/api/time/ntp", json={"server": "secret-time.example.org"}, headers=basic())
    events = structured_events.named("time.server_changed")
    assert len(events) == 1 and events[0].context == {"default": False}
    assert "secret-time" not in repr([e.context for e in structured_events])


# -- the container's status file ------------------------------------------------------------------------------------------------------


def write_status(status_dir, **fields):
    status_dir.mkdir(parents=True, exist_ok=True)
    data = {"at": time.time(), "chronyd": True, "synced": True, "stratum": 3, "offset_ms": 0.4, "server": "pool.ntp.org", "config_error": None}
    data.update(fields)
    (status_dir / "status.json").write_text(json.dumps(data))


def test_no_status_file_means_the_container_is_not_running(tmp_path):
    client, *_ = make(tmp_path)
    container = client.get("/api/time", headers=basic()).json()["container"]
    assert container == {"running": False, "synced": None, "stratum": None, "offset_ms": None, "server": None, "config_error": None, "updated_at": None}


def test_a_fresh_status_file_means_it_is_running_and_says_what_it_sees(tmp_path):
    client, _sync, _q, _c, status = make(tmp_path)
    write_status(status, at=time.time() - 10)
    container = client.get("/api/time", headers=basic()).json()["container"]
    assert container["running"] is True and container["synced"] is True and container["stratum"] == 3 and container["offset_ms"] == 0.4
    assert container["server"] == "pool.ntp.org" and container["config_error"] is None


def test_a_stale_status_file_means_it_is_not_running_even_though_the_file_says_so(tmp_path):
    client, _sync, _q, _c, status = make(tmp_path)
    write_status(status, at=time.time() - ts.STATUS_FRESH_SECONDS - 30)
    container = client.get("/api/time", headers=basic()).json()["container"]
    assert container["running"] is False and container["updated_at"] is not None


def test_a_status_file_dated_in_the_future_is_not_fresh_either(tmp_path):
    client, _sync, _q, _c, status = make(tmp_path)
    write_status(status, at=time.time() + 3600)
    assert client.get("/api/time", headers=basic()).json()["container"]["running"] is False


def test_a_status_that_says_chronyd_is_down_is_not_running(tmp_path):
    client, _sync, _q, _c, status = make(tmp_path)
    write_status(status, chronyd=False)
    assert client.get("/api/time", headers=basic()).json()["container"]["running"] is False


@pytest.mark.parametrize("content", ["", "not json", "[]", "null", '"x"', '{"at": "now"}', '{"at": true, "chronyd": true}', '{"chronyd": true}', "x" * 5000, "\x00\x01"])
def test_a_garbled_status_file_is_a_container_that_is_not_running_never_an_error(tmp_path, content):
    client, _sync, _q, _c, status = make(tmp_path)
    status.mkdir()
    (status / "status.json").write_text(content)
    response = client.get("/api/time", headers=basic())
    assert response.status_code == 200 and response.json()["container"]["running"] is False


def test_odd_values_in_a_good_status_file_are_dropped_not_passed_on(tmp_path):
    client, _sync, _q, _c, status = make(tmp_path)
    write_status(status, stratum="three", offset_ms=[1], server=5, synced="yes", config_error="x" * 1000)
    container = client.get("/api/time", headers=basic()).json()["container"]
    assert container["running"] is True and container["stratum"] is None and container["offset_ms"] is None
    assert container["server"] is None and container["synced"] is None and len(container["config_error"]) == 200


def test_the_status_folder_is_never_written_by_the_api(tmp_path):
    client, _sync, _q, config, status = make(tmp_path)
    client.get("/api/time", headers=basic())
    client.put("/api/time/ntp", json={"server": "time.example.org"}, headers=basic())
    assert not status.exists() and os.listdir(config) == ["ntp.json"]


def test_from_env_reads_its_folders_from_the_environment_and_defaults_to_one_folder(tmp_path):
    sync = ts.TimeSync.from_env({}, tmp_path / "default")
    assert sync.setting.path == tmp_path / "default" / "ntp.json" and sync._status_dir == tmp_path / "default"
    sync = ts.TimeSync.from_env(
        {"MOREOPENREPEATER_TIME_CONFIG_DIR": str(tmp_path / "cfg"), "MOREOPENREPEATER_TIME_STATUS_DIR": str(tmp_path / "stat")}, tmp_path / "default",
    )
    assert sync.setting.path == tmp_path / "cfg" / "ntp.json" and sync._status_dir == tmp_path / "stat"
