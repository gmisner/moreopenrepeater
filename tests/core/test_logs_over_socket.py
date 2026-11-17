"""The core's log, read over the control socket (log aggregation, #229)."""
import asyncio
import logging

import pytest
from control_helpers import get, make_server

from api import remote_core as remote_module
from api.log_hub import LogHub
from api.remote_core import RemoteCore
from contracts import CoreLogs


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(remote_module, "RETRY_DELAYS", (0.05, 0.1))
    monkeypatch.setattr(remote_module, "LOG_POLL_SECONDS", 0.05)


async def until(condition, timeout=5.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not condition():
        assert asyncio.get_running_loop().time() < deadline, "timed out"
        await asyncio.sleep(0.02)


def run(core, sock_dir, scenario, **kwargs):
    async def go():
        path = sock_dir / "core.sock"
        server = make_server(core, path, log_hub=core.log_hub, **kwargs)
        await server.start()
        try:
            return await scenario(path, server)
        finally:
            await server.stop()

    return asyncio.run(go())


def log(core, n, prefix="line"):
    for i in range(n):
        core.log_hub.publish(f"2026-01-01 00:00:00 INFO moreopenrepeater.test: {prefix} {i}", "INFO")


# -- the endpoint --------------------------------------------------------------------------------


def test_get_logs_returns_lines_after_a_cursor_oldest_first(core, sock_dir):
    log(core, 5)

    async def scenario(path, server):
        everything = await get(path, "/logs")
        later = await get(path, "/logs?after=3")
        return everything, later

    (status, body), (_, later) = run(core, sock_dir, scenario)
    first = CoreLogs.model_validate(body)
    assert status == 200 and [l["seq"] for l in first.lines][-5:] == sorted(l["seq"] for l in first.lines)[-5:]
    assert first.cursor == core.log_hub.cursor
    after = CoreLogs.model_validate(later)
    assert all(l["seq"] > 3 for l in after.lines) and after.lines[-1]["seq"] == core.log_hub.cursor
    assert set(first.lines[0]) == {"seq", "line", "level", "at"}


def test_the_limit_is_honoured_and_bounded(core, sock_dir):
    log(core, 600)

    async def scenario(path, server):
        return (await get(path, "/logs?after=0&limit=10"))[1], (await get(path, "/logs?after=0&limit=999999"))[1]

    few, many = run(core, sock_dir, scenario)
    assert len(few["lines"]) == 10 and few["lines"][0]["seq"] == 1  # the oldest ten after the cursor: paging works
    assert len(many["lines"]) <= 500


def test_a_long_line_is_cut(core, sock_dir):
    core.log_hub.publish("x" * 10_000, "INFO")

    async def scenario(path, server):
        return (await get(path, "/logs?after=0"))[1]

    lines = run(core, sock_dir, scenario)["lines"]
    assert [len(l["line"]) for l in lines if l["line"].startswith("xxx")] == [4000]


@pytest.mark.parametrize("query", ["after=x", "limit=y", "after=1.5"])
def test_bad_numbers_are_a_400(core, sock_dir, query):
    async def scenario(path, server):
        return (await get(path, f"/logs?{query}"))[0]

    assert run(core, sock_dir, scenario) == 400


def test_a_core_with_no_hub_answers_with_nothing(core, sock_dir):
    async def go():
        path = sock_dir / "core.sock"
        server = make_server(core, path)  # no log hub
        await server.start()
        try:
            return await get(path, "/logs")
        finally:
            await server.stop()

    status, body = asyncio.run(go())
    assert status == 200 and body["lines"] == []


def test_only_get_is_allowed(core, sock_dir):
    from control_helpers import parse, raw

    async def scenario(path, server):
        return parse(await raw(path, b"POST /logs HTTP/1.1\r\nHost: core\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n{}"))[0]

    assert run(core, sock_dir, scenario) in (404, 405)


def test_the_core_logs_into_its_hub(core):
    logging.getLogger("moreopenrepeater.test").info("a marker line")
    # build_core attached the hub to the shared logger (another core built later may have replaced it)
    lines, _ = core.log_hub.since(0)
    assert lines  # it logged while starting up


# -- RemoteCore: the puller ---------------------------------------------------------------------------


def test_the_remote_core_publishes_the_core_s_lines_into_the_hub_in_order(core, sock_dir):
    log(core, 30)
    hub = LogHub()

    async def scenario(path, server):
        remote = RemoteCore(path)
        remote.attach_log_hub(hub)
        remote.start()
        try:
            await until(lambda: len(hub.since(0, 2000)[0]) >= core.log_hub.cursor)
            core.log_hub.publish("2026-01-01 00:00:01 WARNING moreopenrepeater.test: later", "WARNING")
            await until(lambda: hub.since(0, 2000)[0][-1].line.endswith("later"))
        finally:
            await remote.stop()

    run(core, sock_dir, scenario)
    lines = hub.since(0, 2000)[0]
    assert [l.line for l in lines] == [e.line for e in core.log_hub.since(0, 2000)[0]]  # same lines, same order, once each
    assert lines[-1].level == "WARNING"
    assert lines[-1].at == core.log_hub.since(0, 2000)[0][-1].at  # when the core logged it, not when we read it


def test_nothing_is_published_twice_across_many_polls(core, sock_dir):
    log(core, 3)
    hub = LogHub()

    async def scenario(path, server):
        remote = RemoteCore(path)
        remote.attach_log_hub(hub)
        remote.start()
        try:
            await until(lambda: len(hub.since(0, 2000)[0]) >= core.log_hub.cursor)
            n = len(hub.since(0, 2000)[0])
            await asyncio.sleep(0.4)  # several more polls with nothing new
            assert len(hub.since(0, 2000)[0]) == n
        finally:
            await remote.stop()

    run(core, sock_dir, scenario)


def test_a_backlog_bigger_than_one_read_is_caught_up_at_once(core, sock_dir):
    log(core, 1400)
    hub = LogHub(capacity=3000)

    async def scenario(path, server):
        remote = RemoteCore(path)
        remote.attach_log_hub(hub)
        remote.start()
        try:
            await until(lambda: len(hub.since(0, 3000)[0]) >= 1400, timeout=3)
        finally:
            await remote.stop()

    run(core, sock_dir, scenario)


def test_after_the_core_restarts_its_new_log_is_read_from_the_start(core, sock_dir):
    log(core, 5, "old")
    hub = LogHub()

    async def scenario(path, server):
        remote = RemoteCore(path)
        remote.attach_log_hub(hub)
        remote.start()
        try:
            await until(lambda: any("old 4" in l.line for l in hub.since(0, 2000)[0]))
            await server.stop()  # the core goes away...
            core.log_hub = LogHub()  # ...and comes back with a new log (its sequence starts over)
            log(core, 3, "new")
            server2 = make_server(core, path, log_hub=core.log_hub)
            await server2.start()
            try:
                await until(lambda: any("new 2" in l.line for l in hub.since(0, 2000)[0]))
            finally:
                await server2.stop()
        finally:
            await remote.stop()

    asyncio.run(_wrap(core, sock_dir, scenario))


async def _wrap(core, sock_dir, scenario):
    path = sock_dir / "core.sock"
    server = make_server(core, path, log_hub=core.log_hub)
    await server.start()
    await scenario(path, server)


def test_a_core_that_is_down_is_not_an_error(core, sock_dir):
    hub = LogHub()

    async def go():
        remote = RemoteCore(sock_dir / "nothing.sock")
        remote.attach_log_hub(hub)
        remote.start()
        await asyncio.sleep(0.3)
        await remote.stop()

    asyncio.run(go())
    assert hub.since(0)[0] == []


def test_the_hub_must_be_attached_before_start(sock_dir):
    async def go():
        remote = RemoteCore(sock_dir / "x.sock")
        remote.start()
        try:
            with pytest.raises(RuntimeError):
                remote.attach_log_hub(LogHub())
        finally:
            await remote.stop()

    asyncio.run(go())
