"""Sessions and counters on the Winlink page: what the API makes of the files the gateway and modem containers write (ADR 0006).

The files are the reporter's own output (docker/winlink-reporter.py run over real LinBPQ and Dire Wolf captures), so the two halves
are tested against each other. The files are not trusted: the API reads them as it would anything a container wrote."""
import asyncio
import importlib.util
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.winlink_activity import (
    MAX_FILE_BYTES,
    READ_TIMEOUT_SECONDS,
    SESSIONS_SHOWN,
    WinlinkActivity,
)
from controller import station_clock

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "winlink" / "fixtures"
spec = importlib.util.spec_from_file_location("winlink_reporter", ROOT / "docker" / "winlink-reporter.py")
reporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reporter)

DAY = datetime(2026, 11, 21, tzinfo=timezone.utc).timestamp()
NOON = DAY + 12 * 3600


def run(coro):
    return asyncio.run(coro)


def real_snapshots(folder: Path, now: float = NOON) -> None:
    """The reporter's own files, from the real captures, as they would be written a minute after the last session."""
    tracker = reporter.GatewayTracker(clock=lambda: now)
    tracker.feed_adif((FIXTURES / "bpq_cms_adif_202611.adi").read_bytes().decode("latin-1"))
    tracker.feed_connects(DAY, (FIXTURES / "ConnectLog_261121.log").read_bytes().decode("latin-1"))
    reporter.write_snapshot(folder / "gateway.json", tracker.snapshot(now))
    modem = reporter.ModemCounter(clock=lambda: now - 30)
    for line in (FIXTURES / "direwolf_stdout.txt").read_bytes().splitlines():
        modem.feed(line)
    reporter.write_snapshot(folder / "modem.json", modem.snapshot(now))


def activity(folder, now=NOON, **kwargs):
    clock = kwargs.pop("clock", None) or (lambda: now)
    return WinlinkActivity(folder, clock=clock, **kwargs)


@pytest.fixture(autouse=True)
def utc_station():
    station_clock.apply_timezone("UTC")


# -- reading the reporter's files ----------------------------------------------------------------------------------------------


def test_the_real_gateway_and_modem_files_become_counters_and_a_session_list(tmp_path):
    real_snapshots(tmp_path)
    result = run(activity(tmp_path).read())
    gateway, modem = result["gateway"], result["modem"]
    c = gateway["counters"]
    assert (c["sessions_total"], c["sessions_today"], c["incomplete"]) == (2, 2, 1)
    assert (c["messages_in"], c["messages_out"], c["bytes_in"], c["bytes_out"]) == (1, 1, 120, 300)
    assert (c["connects"], c["failed_connects"]) == (3, 1)
    assert c["last_session_at"] == "2026-11-21T01:33:07Z" and c["since"] == "2026-11-21T12:00:00Z"
    assert [s["callsign"] for s in gateway["sessions"]] == ["N0CALL-1", "N0CALL-1"]
    newest, oldest = gateway["sessions"]  # newest first
    assert newest["started_at"] == "2026-11-21T01:32:53Z" and newest["duration_s"] == 14 and newest["outcome"] == "ended_early"
    assert oldest["started_at"] == "2026-11-21T01:29:52Z" and oldest["outcome"] == "completed"
    assert (oldest["messages_in"], oldest["messages_out"], oldest["bytes_in"], oldest["bytes_out"]) == (1, 1, 120, 300)
    assert modem["counters"]["frames_heard"] == 3 and modem["counters"]["frames_sent"] == 1
    assert modem["counters"]["last_heard_at"] == "2026-11-21T11:59:30Z"
    assert result["notes"] == []


def test_today_follows_the_stations_own_clock_not_utc(tmp_path):
    real_snapshots(tmp_path)
    assert run(activity(tmp_path).read())["gateway"]["counters"]["sessions_today"] == 2
    tomorrow = activity(tmp_path, now=NOON + 86400, stale_seconds=10**9)
    assert run(tomorrow.read())["gateway"]["counters"]["sessions_today"] == 0
    if "America/Los_Angeles" in station_clock.zone_names():
        station_clock.apply_timezone("America/Los_Angeles")  # 01:30 UTC is the evening before there
        assert run(activity(tmp_path).read())["gateway"]["counters"]["sessions_today"] == 0
        station_clock.apply_timezone("America/Los_Angeles")


def test_a_full_session_list_says_today_is_at_least_that_many(tmp_path):
    tracker = reporter.GatewayTracker(clock=lambda: NOON)
    for n in range(reporter.MAX_SESSIONS + 5):
        start = DAY + 3600 + n
        tracker.add_record({"id": f"{n:012x}", "callsign": "K1ABC", "start": start, "end": start + 1, "duration_s": 1,
                            "messages_in": 0, "messages_out": 0, "bytes_in": 0, "bytes_out": 0, "outcome": "completed"})
    reporter.write_snapshot(tmp_path / "gateway.json", tracker.snapshot(NOON))
    c = run(activity(tmp_path).read())["gateway"]["counters"]
    assert c["sessions_today"] == reporter.MAX_SESSIONS and c["sessions_today_is_minimum"] is True
    assert c["sessions_total"] == reporter.MAX_SESSIONS + 5  # the real total comes from the counter, not the list


def test_the_page_gets_the_newest_sessions_only(tmp_path):
    tracker = reporter.GatewayTracker(clock=lambda: NOON)
    for n in range(SESSIONS_SHOWN + 10):
        start = DAY + 3600 + n * 60
        tracker.add_record({"id": f"{n:012x}", "callsign": "K1ABC", "start": start, "end": start + 5, "duration_s": 5,
                            "messages_in": 0, "messages_out": 0, "bytes_in": 0, "bytes_out": 0, "outcome": "completed"})
    reporter.write_snapshot(tmp_path / "gateway.json", tracker.snapshot(NOON))
    sessions = run(activity(tmp_path).read())["gateway"]["sessions"]
    assert len(sessions) == SESSIONS_SHOWN
    assert sessions[0]["started_at"] > sessions[-1]["started_at"]
    assert sessions[0]["started_at"] == datetime.fromtimestamp(DAY + 3600 + (SESSIONS_SHOWN + 9) * 60, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# -- what is missing, old or wrong ---------------------------------------------------------------------------------------------


def test_with_no_folder_set_there_is_no_activity_at_all():
    assert run(WinlinkActivity(None).read()) is None


def test_before_the_containers_have_written_anything_it_says_so_in_words(tmp_path):
    result = run(activity(tmp_path).read())
    assert result["gateway"] is None and result["modem"] is None
    assert any("gateway" in note for note in result["notes"]) and any("modem" in note for note in result["notes"])
    result = run(activity(tmp_path / "no-such-folder").read())
    assert result["gateway"] is None and result["modem"] is None


def test_a_file_that_has_stopped_being_updated_is_not_shown_as_current(tmp_path):
    real_snapshots(tmp_path, now=NOON - 300)  # written five minutes ago, heartbeat every 30 s
    result = run(activity(tmp_path).read())
    assert result["gateway"] is None and result["modem"] is None
    assert any("not reporting" in note for note in result["notes"])
    fresh = real_snapshots(tmp_path, now=NOON - 60) or run(activity(tmp_path).read())
    assert fresh["gateway"] is not None  # a minute old is still current


def test_one_container_missing_does_not_hide_the_other(tmp_path):
    real_snapshots(tmp_path)
    (tmp_path / "modem.json").unlink()
    result = run(activity(tmp_path).read())
    assert result["gateway"] is not None and result["modem"] is None
    assert len(result["notes"]) == 1 and "modem" in result["notes"][0]


GATEWAY_OK = {"v": 1, "kind": "gateway", "updated": NOON, "started": NOON - 100,
              "counters": {"sessions": 1, "incomplete": 0, "messages_in": 1, "messages_out": 0, "bytes_in": 10, "bytes_out": 0,
                           "connects": 1, "failed_connects": 0, "last_session_end": NOON - 50},
              "sessions": [{"id": "0123456789ab", "callsign": "N0CALL-1", "start": NOON - 60, "end": NOON - 50, "duration_s": 10,
                            "messages_in": 1, "messages_out": 0, "bytes_in": 10, "bytes_out": 0, "outcome": "completed"}]}


def with_gateway(tmp_path, data):
    (tmp_path / "gateway.json").write_text(data if isinstance(data, str) else json.dumps(data))
    return run(activity(tmp_path).read())


@pytest.mark.parametrize("bad", [
    "not json", "", "[]", "null", "12", '{"v": 2, "kind": "gateway"}', '{"v": 1, "kind": "modem", "updated": 1}',
    json.dumps({**GATEWAY_OK, "updated": "yesterday"}), json.dumps({**GATEWAY_OK, "updated": NOON + 10**7}),
    json.dumps({**GATEWAY_OK, "counters": "many"}), json.dumps({**GATEWAY_OK, "sessions": "many"}),
    json.dumps({k: v for k, v in GATEWAY_OK.items() if k != "counters"}),
])
def test_a_file_that_is_not_what_the_reporter_writes_is_ignored_with_a_note(tmp_path, bad):
    result = with_gateway(tmp_path, bad)
    assert result["gateway"] is None
    assert any("gateway" in note for note in result["notes"])


def test_a_huge_file_is_not_read(tmp_path):
    (tmp_path / "gateway.json").write_text(" " * (MAX_FILE_BYTES + 10) + json.dumps(GATEWAY_OK))
    assert run(activity(tmp_path).read())["gateway"] is None


def test_what_is_in_the_file_is_checked_and_held_to_sane_values(tmp_path):
    hostile = json.loads(json.dumps(GATEWAY_OK))
    hostile["counters"] = {"sessions": 10**30, "incomplete": -4, "messages_in": "3", "messages_out": 2.7, "bytes_in": None,
                           "bytes_out": True, "connects": 5, "failed_connects": 99, "last_session_end": "soon", "extra": "x"}
    hostile["sessions"] = [
        {**GATEWAY_OK["sessions"][0], "callsign": "<b>bold</b>\nW1AW", "outcome": "<script>", "id": "../../etc/passwd",
         "sid": "[RMS Express-1.5]", "messages_in": 10**20, "start": "x"},
        {**GATEWAY_OK["sessions"][0], "id": "aaaaaaaaaaaa", "callsign": "k1abc", "outcome": "ended_early", "messages_in": -3},
        "junk", None, 7, {"id": "bbbbbbbbbbbb"},
    ]
    result = with_gateway(tmp_path, hostile)
    c = result["gateway"]["counters"]
    assert c["sessions_total"] == 10**7  # held down, not trusted
    assert (c["incomplete"], c["messages_in"], c["messages_out"], c["bytes_in"], c["bytes_out"]) == (0, 0, 0, 0, 0)
    assert c["last_session_at"] is None
    sessions = result["gateway"]["sessions"]
    assert [s["callsign"] for s in sessions] == ["K1ABC"]  # the one with a callsign that is not a callsign and an id that is not an id is dropped
    assert sessions[0]["outcome"] == "ended_early" and sessions[0]["messages_in"] == 0
    text = json.dumps(result)
    for word in ("script", "passwd", "RMS Express", "bold", "extra"):
        assert word not in text


@pytest.mark.parametrize("call", ["<b>bold</b>", "W1AW\nroot", "javascript:alert(1)", "A" * 40, 7, None, ["W1AW"]])
def test_a_callsign_that_is_not_one_is_not_passed_on_but_the_session_still_counts(tmp_path, call):
    data = json.loads(json.dumps(GATEWAY_OK))
    data["sessions"][0]["callsign"] = call
    result = with_gateway(tmp_path, data)
    assert result["gateway"]["sessions"][0]["callsign"] is None and result["gateway"]["sessions"][0]["messages_in"] == 1
    assert "bold" not in json.dumps(result) and "alert" not in json.dumps(result)


def test_the_list_it_reads_is_bounded(tmp_path):
    big = json.loads(json.dumps(GATEWAY_OK))
    base = GATEWAY_OK["sessions"][0]
    big["sessions"] = [{**base, "id": f"{n:012x}", "start": NOON - 100 - n, "end": NOON - 90 - n} for n in range(1000)]
    assert len(with_gateway(tmp_path, big)["gateway"]["sessions"]) <= SESSIONS_SHOWN


# -- the event ---------------------------------------------------------------------------------------------------------------------


def session(n, **extra):
    base = GATEWAY_OK["sessions"][0]
    return {**base, "id": f"{n:012x}", "start": NOON - 1000 + n * 10, "end": NOON - 990 + n * 10, **extra}


def publish(folder, sessions, now=NOON, started=None):
    data = {**GATEWAY_OK, "updated": now, "sessions": sessions, "counters": {**GATEWAY_OK["counters"], "sessions": len(sessions)}}
    if started is not None:
        data["started"] = started
    (folder / "gateway.json").write_text(json.dumps(data))


def test_a_session_that_ends_is_logged_once_as_a_named_event_with_facts_and_no_callsign(tmp_path, structured_events):
    now = [NOON]
    a = activity(tmp_path, clock=lambda: now[0])
    publish(tmp_path, [session(1)], now[0])
    run(a.read())  # the first read is the baseline: sessions from before the API started are not announced
    assert structured_events.named("winlink.session_ended") == []
    now[0] += 20
    publish(tmp_path, [session(1), session(2, duration_s=61, messages_in=2, messages_out=3, bytes_in=700, bytes_out=900,
                                              outcome="completed")], now[0])
    run(a.read())
    events = structured_events.named("winlink.session_ended")
    assert len(events) == 1
    assert events[0].context["duration_s"] == 61 and events[0].context["messages_in"] == 2
    assert events[0].context["messages_out"] == 3 and events[0].context["outcome"] == "completed"
    assert events[0].context["bytes_in"] == 700 and events[0].context["bytes_out"] == 900
    shown = json.dumps(events[0].context) + str(events[0].message)
    assert "N0CALL" not in shown and "callsign" not in shown.lower()  # a station's callsign does not go to the log
    now[0] += 20
    run(a.read())
    run(a.read())
    assert len(structured_events.named("winlink.session_ended")) == 1  # not again


def test_two_new_sessions_are_two_events_oldest_first(tmp_path, structured_events):
    now = [NOON]
    a = activity(tmp_path, clock=lambda: now[0])
    publish(tmp_path, [], now[0])
    run(a.read())
    now[0] += 20
    publish(tmp_path, [session(2, duration_s=22), session(1, duration_s=11)], now[0])  # the file need not be in order
    run(a.read())
    assert [e.context["duration_s"] for e in structured_events.named("winlink.session_ended")] == [11, 22]


def test_a_reporter_that_starts_over_the_same_sessions_does_not_log_them_again(tmp_path, structured_events):
    now = [NOON]
    a = activity(tmp_path, clock=lambda: now[0])
    publish(tmp_path, [], now[0])
    run(a.read())
    now[0] += 20
    publish(tmp_path, [session(1)], now[0])
    run(a.read())
    now[0] += 20
    gone = {**GATEWAY_OK, "updated": now[0], "started": now[0], "sessions": [session(1)]}  # restarted: read the same log again
    (tmp_path / "gateway.json").write_text(json.dumps(gone))
    run(a.read())
    assert len(structured_events.named("winlink.session_ended")) == 1


def test_a_gateway_that_started_after_the_api_has_nothing_old_in_it(tmp_path, structured_events):
    now = [NOON]
    a = activity(tmp_path, clock=lambda: now[0])
    run(a.read())  # nothing there yet
    now[0] += 20
    publish(tmp_path, [session(1)], now[0], started=NOON + 5)  # it began after the API did: this session is new
    run(a.read())
    assert len(structured_events.named("winlink.session_ended")) == 1


def test_a_gateway_that_was_already_running_when_the_api_started_is_not_announced_for_what_it_did_before(tmp_path, structured_events):
    now = [NOON]
    a = activity(tmp_path, clock=lambda: now[0])
    run(a.read())  # nothing there yet
    now[0] += 20
    publish(tmp_path, [session(1)], now[0], started=NOON - 5000)
    run(a.read())
    assert structured_events.named("winlink.session_ended") == []


# -- the cache and the bound ----------------------------------------------------------------------------------------------------


def test_a_second_read_inside_fifteen_seconds_does_not_touch_the_files_again(tmp_path):
    now = [NOON]
    a = activity(tmp_path, clock=lambda: now[0], stale_seconds=10**6)
    publish(tmp_path, [session(1)], now[0])
    first = run(a.read())
    publish(tmp_path, [session(1), session(2)], now[0])
    now[0] += 14
    assert run(a.read()) is first  # the cached answer
    now[0] += 2
    assert len(run(a.read())["gateway"]["sessions"]) == 2
    assert a.cache_seconds == 15.0 and a.stale_seconds == 10**6


def test_callers_that_arrive_together_share_one_read(tmp_path, monkeypatch):
    real_snapshots(tmp_path)
    a = activity(tmp_path)
    reads = []
    original = a._load_all

    def counting():
        reads.append(1)
        return original()

    monkeypatch.setattr(a, "_load_all", counting)

    async def main():
        return await asyncio.gather(*(a.read() for _ in range(8)))

    run(main())
    assert len(reads) == 1


def test_a_read_that_hangs_gives_up_with_a_note_and_does_not_stall_the_event_loop(tmp_path, monkeypatch):
    real_snapshots(tmp_path)
    a = activity(tmp_path, timeout=0.3)
    monkeypatch.setattr(a, "_load_all", lambda: time.sleep(2))
    assert READ_TIMEOUT_SECONDS == 2.0 and WinlinkActivity(None).timeout == 2.0

    async def main():
        gaps = []

        async def heartbeat():
            last = time.monotonic()
            while True:
                await asyncio.sleep(0.01)
                now = time.monotonic()
                gaps.append(now - last)
                last = now

        ticker = asyncio.ensure_future(heartbeat())
        started = time.monotonic()
        result = await a.read()
        elapsed = time.monotonic() - started
        ticker.cancel()
        return result, elapsed, gaps

    result, elapsed, gaps = run(main())
    assert result["gateway"] is None and any("too long" in n for n in result["notes"])
    assert 0.25 <= elapsed < 1.0 and max(gaps) < 0.1


# -- the route -------------------------------------------------------------------------------------------------------------------


def make_client(monkeypatch, folder, auth=False):
    import tempfile

    from api.app import create_app
    from api.auth import AuthSettings
    from api.service import RepeaterService
    from controller.config import RepeaterConfig

    for name in ("MOREOPENREPEATER_AUTH_USER", "MOREOPENREPEATER_AUTH_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    for name in ("MOREOPENREPEATER_WINLINK_HOST", "MOREOPENREPEATER_WINLINK_MODEM_HOST"):
        monkeypatch.setenv(name, "")
    if folder is None:
        monkeypatch.delenv("MOREOPENREPEATER_WINLINK_STATUS_DIR", raising=False)
    else:
        monkeypatch.setenv("MOREOPENREPEATER_WINLINK_STATUS_DIR", str(folder))
    extra = {"auth_settings": AuthSettings(username="admin", password="hunter2")} if auth else {}
    app = create_app(service=RepeaterService(config=RepeaterConfig()), start_background_tick=False,
                     log_path=Path(tempfile.mkdtemp()) / "test.log", **extra)
    return TestClient(app)


def test_the_route_carries_the_activity_next_to_the_checks(monkeypatch, tmp_path):
    now = time.time()
    real_snapshots(tmp_path, now=now)
    body = make_client(monkeypatch, tmp_path).get("/api/winlink").json()
    assert body["enabled"] is False and body["activity"]["gateway"]["counters"]["sessions_total"] == 2
    assert body["activity"]["modem"]["counters"]["frames_heard"] == 3
    assert [s["callsign"] for s in body["activity"]["gateway"]["sessions"]] == ["N0CALL-1", "N0CALL-1"]


def test_without_a_status_folder_the_activity_is_null_and_the_rest_is_as_before(monkeypatch):
    body = make_client(monkeypatch, None).get("/api/winlink").json()
    assert body["activity"] is None and set(body) == {"enabled", "callsign", "checks", "problems", "activity"}


def test_the_activity_needs_the_same_login_as_the_rest(monkeypatch, tmp_path):
    real_snapshots(tmp_path, now=time.time())
    client = make_client(monkeypatch, tmp_path, auth=True)
    assert client.get("/api/winlink").status_code == 401
    assert client.get("/api/winlink", auth=("admin", "hunter2")).json()["activity"]["gateway"] is not None


def test_nothing_secret_or_free_form_is_in_the_response(monkeypatch, tmp_path):
    data = json.loads(json.dumps(GATEWAY_OK))
    data["updated"] = time.time()
    data["password"] = "hunter2-REAL"
    data["sessions"][0]["sid"] = "[RMS Express-1.5.0.0]"
    data["sessions"][0]["text"] = "PRIVATE MESSAGE BODY"
    (tmp_path / "gateway.json").write_text(json.dumps(data))
    text = make_client(monkeypatch, tmp_path).get("/api/winlink").text
    for word in ("hunter2", "RMS Express", "PRIVATE", "password"):
        assert word not in text
    assert os.path.exists(tmp_path / "gateway.json")  # and the API never writes there: it only reads
