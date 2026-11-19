"""The core keeps the activity history and serves it (Stage 3 slice 7c-8, #229). A real control
server and a real `RemoteActivityStore`; the dashboard's reads of it come from the core."""
import asyncio

import pytest
from control_helpers import get, make_server

from api.activity import ActivityRow
from api.remote_activity import RemoteActivityStore
from api.service.core_handle import CoreUnavailable
from contracts import ActivityRows
from core import control as control_module
from core.build import build_core


def run(core, sock_dir, scenario):
    async def go():
        path = sock_dir / "core.sock"
        server = make_server(core, path, activity_store=core.activity_store)
        await server.start()
        try:
            return await scenario(path, server)
        finally:
            await server.stop()

    return asyncio.run(go())


def record(core, *rows):
    for row in rows:
        core.activity_store.add(row)


def rx(at, seconds=2.0, timed_out=False):
    return ActivityRow("rx", at, seconds, timed_out)


async def off_loop(function, *args):
    """The store reads over a blocking socket call, as it does in a route; the server is on this loop."""
    return await asyncio.get_running_loop().run_in_executor(None, function, *args)


# -- the core keeps it ------------------------------------------------------------------------


def test_the_core_keeps_its_history_in_its_data_folder_across_a_restart(core):
    record(core, rx(1000.0), ActivityRow("id", 1100.0, 0.0))
    core.activity_store.flush()
    data = core.state_path.parent
    assert (data / "activity.db").exists()
    again = build_core({"MOREOPENREPEATER_DATA_DIR": str(data), "MOREOPENREPEATER_LOG_PATH": str(data / "c.log")})
    kept = again.activity_store.rows(0, 2000)
    assert [(r.kind, r.started_at) for r in kept] == [("rx", 1000.0), ("id", 1100.0)]


def test_the_recorder_the_service_drives_writes_into_that_history(core):
    recorder = core.service.activity
    recorder.state_changed("idle", "receiving", 5000.0)
    recorder.state_changed("receiving", "idle", 5004.0)
    assert [(r.kind, r.duration) for r in core.activity_store.rows(0, 9999)] == [("rx", 4.0)]


# -- GET /activity ----------------------------------------------------------------------------


def test_get_activity_returns_the_rows_in_the_window_oldest_first(core, sock_dir):
    record(core, rx(300.0), rx(100.0), rx(200.0), rx(900.0))

    async def scenario(path, server):
        return await get(path, "/activity?since=150&until=500")

    status, body = run(core, sock_dir, scenario)
    rows = ActivityRows.model_validate(body)
    assert status == 200 and [r.started_at for r in rows.rows] == [200.0, 300.0] and rows.truncated is False


def test_a_kind_filter_and_the_newest_first_form(core, sock_dir):
    record(core, rx(100.0), ActivityRow("tx", 110.0, 3.0), rx(200.0, timed_out=True), ActivityRow("rx", 300.0, 1.0))

    async def scenario(path, server):
        only_tx = await get(path, "/activity?since=0&until=999&kind=tx")
        newest = await get(path, "/activity?kind=rx&newest=1&limit=2")
        return only_tx, newest

    only_tx, newest = run(core, sock_dir, scenario)
    assert [r["kind"] for r in only_tx[1]["rows"]] == ["tx"]
    assert [r["started_at"] for r in newest[1]["rows"]] == [300.0, 200.0]
    assert newest[1]["rows"][1]["timed_out"] is True


def test_too_many_rows_are_cut_and_say_so(core, sock_dir, monkeypatch):
    monkeypatch.setattr(control_module, "MAX_ACTIVITY_ROWS", 3)
    record(core, *[rx(float(i)) for i in range(10, 20)])

    async def scenario(path, server):
        return await get(path, "/activity?since=0&until=999")

    status, body = run(core, sock_dir, scenario)
    assert status == 200 and len(body["rows"]) == 3 and body["truncated"] is True
    assert [r["started_at"] for r in body["rows"]] == [10.0, 11.0, 12.0]  # the oldest, so a reader can go on


@pytest.mark.parametrize(
    "target",
    ["/activity?since=abc", "/activity?until=x", "/activity?limit=many", "/activity?newest=1"],
)
def test_bad_requests_are_refused_not_crashed_on(core, sock_dir, target):
    async def scenario(path, server):
        return await get(path, target), await get(path, "/status")

    (status, body), (alive, _) = run(core, sock_dir, scenario)
    assert status == 400 and "error" in body and alive == 200


def test_the_paths_method_is_checked(core, sock_dir):
    from control_helpers import parse, raw

    async def scenario(path, server):
        return parse(await raw(path, b"POST /activity HTTP/1.1\r\nHost: core\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n{}"))

    status, body = run(core, sock_dir, scenario)
    assert status == 405 and "GET" in body["error"]


def test_a_core_with_no_store_answers_with_nothing(core, sock_dir):
    async def go():
        path = sock_dir / "core.sock"
        server = make_server(core, path)  # no activity_store
        await server.start()
        try:
            return await get(path, "/activity?since=0")
        finally:
            await server.stop()

    status, body = asyncio.run(go())
    assert status == 200 and body["rows"] == [] and body["truncated"] is False


# -- the API's store, reading it over the socket ------------------------------------------------


def test_the_remote_store_reads_what_the_core_recorded(core, sock_dir):
    record(core, rx(100.0), ActivityRow("id", 150.0, 0.0), rx(200.0, timed_out=True), rx(300.0))

    async def scenario(path, server):
        store = RemoteActivityStore(path)
        window = await off_loop(store.rows, 120.0, 250.0)
        only_rx = await off_loop(store.rows, 0.0, 999.0, "rx")
        latest = await off_loop(store.recent, "rx", 2)
        return window, only_rx, latest

    window, only_rx, latest = run(core, sock_dir, scenario)
    assert [(r.kind, r.started_at) for r in window] == [("id", 150.0), ("rx", 200.0)]
    assert window[1].timed_out is True
    assert [r.started_at for r in only_rx] == [100.0, 200.0, 300.0]
    assert [r.started_at for r in latest] == [300.0, 200.0]
    assert only_rx == core.activity_store.rows(0.0, 999.0, "rx")


def test_the_remote_store_pages_through_a_window_longer_than_one_answer(core, sock_dir, monkeypatch):
    monkeypatch.setattr(control_module, "MAX_ACTIVITY_ROWS", 4)
    # 13 rows, three of them at one instant and two pairs of identical ones, across several pages.
    times = [10.0, 11.0, 12.0, 12.0, 12.0, 13.0, 14.0, 14.0, 15.0, 16.0, 17.0, 17.0, 18.0]
    record(core, *[rx(t) for t in times])

    async def scenario(path, server):
        return await off_loop(RemoteActivityStore(path).rows, 0.0, 999.0)

    found = run(core, sock_dir, scenario)
    assert [r.started_at for r in found] == times  # every row once, none dropped or doubled at a page edge


def test_with_the_core_away_the_remote_store_says_so_and_does_not_return_nothing(sock_dir):
    store = RemoteActivityStore(sock_dir / "nothing.sock")
    with pytest.raises(CoreUnavailable):
        store.rows(0.0, 10.0)
    with pytest.raises(CoreUnavailable):
        store.recent("rx", 5)


def test_a_backup_gets_an_empty_history_file_and_a_restore_does_not_pretend(tmp_path, caplog):
    import logging
    import sqlite3

    store = RemoteActivityStore(tmp_path / "unused.sock")
    store.copy_to(tmp_path / "activity.db")
    assert sqlite3.connect(tmp_path / "activity.db").execute("SELECT COUNT(*) FROM activity").fetchone() == (0,)
    with caplog.at_level(logging.WARNING, logger="moreopenrepeater.remote_activity"):
        store.replace_from(tmp_path / "activity.db")
    assert "not applied" in caplog.text
    store.flush()
    assert store.prune(0.0) == 0  # the core does its own
