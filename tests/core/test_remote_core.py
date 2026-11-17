"""`RemoteCore`: the API's read-only view of a core in its own process (Stage 3 slice 7c-2,
#229). It follows the core's real `GET /events` over a real Unix socket. Simulation only."""
import asyncio
import dataclasses
import json
import subprocess
import sys
from pathlib import Path

import pytest
from control_helpers import make_server

from api import remote_core as remote_module
from api.remote_core import UNAVAILABLE_STATE, RemoteCore
from core import control as control_module


@pytest.fixture(autouse=True)
def quick_retries(monkeypatch):
    monkeypatch.setattr(remote_module, "RETRY_DELAYS", (0.05, 0.1))


async def until(condition, timeout=5.0, interval=0.02):
    deadline = asyncio.get_running_loop().time() + timeout
    while not condition():
        assert asyncio.get_running_loop().time() < deadline, "timed out"
        await asyncio.sleep(interval)


COUNTDOWNS = ("tot_remaining", "hang_remaining", "id_due_in")


def same(core_snapshot, remote_snapshot, tolerance=0.75):
    """Every field the dashboard reads. The timestamp is stamped by each side; the
    countdowns are carried forward by the remote side, so they match within a moment."""
    a, b = dataclasses.asdict(core_snapshot), dataclasses.asdict(remote_snapshot)
    a.pop("timestamp"), b.pop("timestamp")
    for name in COUNTDOWNS:
        x, y = a.pop(name), b.pop(name)
        if (x is None) != (y is None) or (x is not None and abs(x - y) > tolerance):
            return False
    return a == b


def run(core, sock_dir, scenario, **kwargs):
    async def go():
        path = sock_dir / "core.sock"
        server = make_server(core, path, **kwargs)
        await server.start()
        remote = RemoteCore(path)
        remote.start()
        try:
            return await scenario(path, server, remote)
        finally:
            await remote.stop()
            await server.stop()

    return asyncio.run(go())


# --- it mirrors the core -------------------------------------------------------------------

def test_the_remote_snapshot_matches_the_in_process_one_through_a_series_of_states(core, sock_dir):
    async def scenario(path, server, remote):
        await until(lambda: remote.connected)
        steps = [
            lambda: None,
            lambda: core.service.simulate_cos(active=True),
            lambda: core.service.simulate_ctcss(100.0),
            lambda: core.service.set_net_active(True),
            lambda: core.service.simulate_cos(active=False),
            lambda: core.service.simulate_remote_keyed("allstar", True),
            lambda: core.service.simulate_remote_keyed("allstar", False),
            lambda: core.service.update_config(transmitter_enabled=False),
        ]
        for step in steps:
            step()
            await until(lambda: same(core.service.snapshot(), remote.snapshot()), timeout=3)
        final = remote.snapshot()
        assert final.callsign == "W1AW" and final.transmitter_enabled is False and final.net_active is True

    run(core, sock_dir, scenario)


def test_ptt_active_stays_the_controllers_intent_and_nothing_more_is_claimed(core, sock_dir):
    async def scenario(path, server, remote):
        await until(lambda: remote.connected)
        core.service.simulate_cos(active=True)
        await until(lambda: remote.snapshot().state == "receiving")
        assert remote.snapshot().ptt_active is True
        assert remote.status.ptt_commanded is True
        assert remote.status.ptt_observed is None and remote.status.ptt_evidence.value == "none"

    run(core, sock_dir, scenario)


def test_the_stale_flag_and_age_and_the_core_health_from_the_heartbeat(core, sock_dir, monkeypatch):
    monkeypatch.setattr(control_module, "HEARTBEAT_SECONDS", 0.1)
    now = {"t": 100.0}

    async def scenario(path, server, remote):
        await until(lambda: remote.core_health is not None)
        assert remote.core_health in ("ok", "failed")
        assert remote.connected and not remote.stale
        remote._clock = lambda: now["t"] + 1000  # time passes with nothing heard
        assert remote.age_s > 100 and remote.stale
        assert remote.connected  # still connected, just not heard from: stale says so

    remote_module_clock = lambda: now["t"]  # noqa: E731

    async def go():
        path = sock_dir / "core.sock"
        server = make_server(core, path)
        await server.start()
        remote = RemoteCore(path, clock=remote_module_clock)
        remote.start()
        try:
            await scenario(path, server, remote)
        finally:
            await remote.stop()
            await server.stop()

    asyncio.run(go())


def test_subscribers_see_changes_and_unsubscribed_ones_stop(core, sock_dir):
    async def scenario(path, server, remote):
        await until(lambda: remote.connected)
        queue = remote.subscribe()
        core.service.simulate_cos(active=True)
        snapshot = await asyncio.wait_for(queue.get(), 3)
        assert snapshot.state == "receiving"
        remote.unsubscribe(queue)
        core.service.simulate_cos(active=False)
        await until(lambda: remote.snapshot().state != "receiving")
        assert queue.empty()

    run(core, sock_dir, scenario)


# --- the core is not there, goes away or comes back ---------------------------------------------

def test_with_no_core_running_it_says_so_and_does_not_raise(sock_dir):
    async def go():
        remote = RemoteCore(sock_dir / "nothing.sock")
        remote.start()  # must not raise or block
        await asyncio.sleep(0.3)
        snapshot = remote.snapshot()
        assert snapshot.state == UNAVAILABLE_STATE and snapshot.ptt_active is False
        assert remote.connected is False and remote.stale and remote.age_s is None and remote.status is None
        assert remote.reconnects >= 2  # it keeps trying
        await remote.stop()

    asyncio.run(go())


def test_it_connects_when_the_core_starts_later(core, sock_dir):
    async def go():
        path = sock_dir / "core.sock"
        remote = RemoteCore(path)
        remote.start()
        await asyncio.sleep(0.3)
        assert not remote.connected
        server = make_server(core, path)
        await server.start()
        try:
            await until(lambda: remote.connected)
            assert remote.snapshot().state == "idle" and not remote.stale
        finally:
            await remote.stop()
            await server.stop()

    asyncio.run(go())


def test_it_notices_the_core_going_away_and_comes_back_when_it_returns(core, sock_dir):
    async def go():
        path = sock_dir / "core.sock"
        server = make_server(core, path)
        await server.start()
        remote = RemoteCore(path)
        remote.start()
        queue = remote.subscribe()
        try:
            await until(lambda: remote.connected)
            await server.stop()  # the core goes away
            await until(lambda: not remote.connected)
            assert remote.stale and remote.snapshot().state != UNAVAILABLE_STATE  # the last known value is kept
            seen = []
            while not queue.empty():
                seen.append(queue.get_nowait().state)
            assert seen[-1] == UNAVAILABLE_STATE  # and subscribers were told it is no longer confirmed
            server = make_server(core, path)
            await server.start()  # the core returns
            await until(lambda: remote.connected)
            assert not remote.stale and remote.reconnects >= 1
        finally:
            await remote.stop()
            await server.stop()

    asyncio.run(go())


def test_a_stream_that_goes_quiet_is_dropped_and_reopened(core, sock_dir, monkeypatch):
    monkeypatch.setattr(remote_module, "STALE_AFTER_SECONDS", 0.4)
    monkeypatch.setattr(control_module, "HEARTBEAT_SECONDS", 60.0)  # the core says nothing after the first event

    async def scenario(path, server, remote):
        await until(lambda: remote.connected)
        await until(lambda: remote.reconnects >= 1, timeout=5)  # it gave up on the silent stream and retried
        await until(lambda: remote.connected)

    run(core, sock_dir, scenario)


def test_a_refused_stream_is_retried(core, sock_dir, monkeypatch):
    monkeypatch.setattr(control_module, "MAX_STREAMS", 0)  # every /events gets a 503

    async def scenario(path, server, remote):
        await asyncio.sleep(0.4)
        assert not remote.connected and remote.reconnects >= 2
        monkeypatch.setattr(control_module, "MAX_STREAMS", 4)
        await until(lambda: remote.connected)

    run(core, sock_dir, scenario)


def test_a_malformed_event_is_skipped_and_the_stream_goes_on(sock_dir):
    async def go():
        path = sock_dir / "fake.sock"

        async def handler(reader, writer):
            await reader.readuntil(b"\r\n\r\n")
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nConnection: close\r\n\r\n")
            writer.write(b": a comment\n\nevent: status\ndata: {not json\n\nevent: status\ndata: {\"status\": 5}\n\n")
            writer.write(b"event: heartbeat\ndata: {\"health\": \"ok\"}\n\n")
            await writer.drain()
            await asyncio.sleep(5)

        server = await asyncio.start_unix_server(handler, path=str(path))
        remote = RemoteCore(path)
        remote.start()
        try:
            await until(lambda: remote.core_health == "ok")
            assert remote.status is None and remote.snapshot().state == UNAVAILABLE_STATE
        finally:
            await remote.stop()
            server.close()

    asyncio.run(go())


# --- scope -------------------------------------------------------------------------------------

def test_writes_only_go_through_the_core_s_own_answer():
    remote = RemoteCore(Path("/nonexistent"))
    for write in ("update_config", "clear_lockout", "set_net_active", "simulate_cos", "import_snapshot"):
        assert not hasattr(remote, write), f"RemoteCore must not offer {write} yet"
    # The ways it can act, each of which waits for (or, for speak, hands off to) the core.
    assert all(hasattr(remote, name) for name in ("change_config", "command", "render_and_queue", "speak"))


def test_remote_core_imports_no_web_framework():
    names = ("fastapi", "starlette", "api.app", "api.runtime")
    code = (
        "import sys, json; sys.path.insert(0, sys.argv[1]);"
        "import api.remote_core;"
        f"print(json.dumps({{n: n in sys.modules for n in {names!r}}}))"
    )
    src = str(Path(__file__).resolve().parents[2] / "src")
    out = subprocess.run([sys.executable, "-I", "-c", code, src], capture_output=True, text=True, timeout=60, check=True)
    assert json.loads(out.stdout.strip().splitlines()[-1]) == dict.fromkeys(names, False)


def test_countdowns_run_down_while_connected_and_freeze_when_stale(core, sock_dir):
    now = {"t": 1000.0}

    async def go():
        path = sock_dir / "core.sock"
        server = make_server(core, path)
        await server.start()
        remote = RemoteCore(path, clock=lambda: now["t"])
        remote.start()
        try:
            await until(lambda: remote.connected)
            core.service.simulate_cos(active=True)
            await until(lambda: remote.snapshot().state == "receiving")
            first = remote.snapshot()
            assert first.tot_remaining and first.id_due_in
            now["t"] += 10  # ten seconds pass with no new event, but the core is still heard from
            remote._last_event_at = now["t"]
            later = remote.snapshot()
            assert later.tot_remaining == pytest.approx(first.tot_remaining - 10, abs=0.5)
            assert later.id_due_in == pytest.approx(first.id_due_in - 10, abs=0.5)
            now["t"] += 10_000  # the core goes quiet: nothing more is claimed
            frozen = remote.snapshot()
            assert frozen.tot_remaining == first.tot_remaining and frozen.id_due_in == first.id_due_in
            now["t"] += 5
            assert remote.snapshot() == frozen
        finally:
            await remote.stop()
            await server.stop()

    asyncio.run(go())


def test_a_countdown_never_goes_below_zero(core, sock_dir):
    now = {"t": 0.0}

    async def go():
        path = sock_dir / "core.sock"
        server = make_server(core, path)
        await server.start()
        remote = RemoteCore(path, clock=lambda: now["t"])
        remote.start()
        try:
            await until(lambda: remote.connected)
            core.service.simulate_cos(active=True)
            await until(lambda: remote.snapshot().state == "receiving")
            now["t"] += 500
            remote._last_event_at = now["t"]
            assert remote.snapshot().tot_remaining == 0.0
        finally:
            await remote.stop()
            await server.stop()

    asyncio.run(go())
