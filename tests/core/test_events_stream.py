"""GET /events on the core's control socket (Stage 3 slice 7c-2, #229): status changes
and a heartbeat as server-sent events. Simulation only: no devices, no Pi."""
import asyncio
import dataclasses
import json
import socket
import time

from control_helpers import make_server, parse, raw

from contracts import CoreStatus
from core import control as control_module

REQUEST = b"GET /events HTTP/1.1\r\nHost: core\r\nAccept: text/event-stream\r\n\r\n"


async def open_stream(path):
    reader, writer = await asyncio.open_unix_connection(str(path))
    writer.write(REQUEST)
    await writer.drain()
    head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
    return reader, writer, head


async def next_event(reader, timeout=5):
    """One server-sent event as (name, data)."""
    name, data = "message", []
    while True:
        line = (await asyncio.wait_for(reader.readline(), timeout)).decode().rstrip("\n")
        if line == "":
            if data:
                return name, json.loads("\n".join(data))
            continue
        field, _, value = line.partition(":")
        value = value.lstrip(" ")
        if field == "event":
            name = value
        elif field == "data":
            data.append(value)


def run(core, sock_dir, scenario, **kwargs):
    async def go():
        server = make_server(core, sock_dir / "core.sock", **kwargs)
        await server.start()
        try:
            return await scenario(sock_dir / "core.sock", server)
        finally:
            await server.stop()

    return asyncio.run(go())


def test_the_stream_starts_with_the_current_status(core, sock_dir):
    async def scenario(path, server):
        reader, writer, head = await open_stream(path)
        assert head.startswith(b"HTTP/1.1 200") and b"text/event-stream" in head
        name, data = await next_event(reader)
        assert name == "status"
        status = CoreStatus.model_validate(data["status"])
        assert status.state == "idle"
        assert status.callsign == "W1AW" and status.net_active is False and status.linked_nodes == []
        assert set(data["local"]) == {"last_clip", "timestamp", "config_version"}  # the API-only extras
        writer.close()

    run(core, sock_dir, scenario)


def test_a_change_is_pushed_and_the_stream_follows_it(core, sock_dir):
    async def scenario(path, server):
        reader, writer, _ = await open_stream(path)
        await next_event(reader)  # the initial status
        core.service.simulate_cos(active=True)
        name, data = await next_event(reader)
        assert name == "status"
        status = CoreStatus.model_validate(data["status"])
        assert status.state == "receiving" and status.cos_active and status.ptt_commanded
        assert status.ptt_observed is None  # still no claim about RF
        core.service.simulate_cos(active=False)
        _, data = await next_event(reader)
        assert CoreStatus.model_validate(data["status"]).cos_active is False
        writer.close()

    run(core, sock_dir, scenario)


def test_a_heartbeat_arrives_when_nothing_changes(core, sock_dir, monkeypatch):
    monkeypatch.setattr(control_module, "HEARTBEAT_SECONDS", 0.2)

    async def scenario(path, server):
        reader, writer, _ = await open_stream(path)
        await next_event(reader)
        name, data = await next_event(reader)
        assert name == "heartbeat" and data["health"] in ("ok", "failed") and "seq" in data and "at" in data
        _, again = await next_event(reader)
        assert again["seq"] > data["seq"]
        writer.close()

    run(core, sock_dir, scenario)


def test_a_burst_of_changes_is_coalesced_to_the_latest(core, sock_dir, monkeypatch):
    monkeypatch.setattr(control_module, "MIN_EVENT_INTERVAL_SECONDS", 0.3)

    async def scenario(path, server):
        reader, writer, _ = await open_stream(path)
        await next_event(reader)
        for i in range(20):
            core.service.simulate_cos(active=bool(i % 2))
            core.service.simulate_ctcss(100.0 + i)
        core.service.simulate_ctcss(123.0)
        seen = []
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try:
                name, data = await next_event(reader, timeout=0.6)
            except asyncio.TimeoutError:
                break
            if name == "status":
                seen.append(data["status"]["ctcss_hz"])
        assert seen[-1] == 123.0  # it ended on the latest value
        assert len(seen) < 10  # far fewer than the 41 changes
        writer.close()

    run(core, sock_dir, scenario)


def test_a_client_that_hangs_up_is_cleaned_up(core, sock_dir):
    async def scenario(path, server):
        for _ in range(3):
            reader, writer, _ = await open_stream(path)
            await next_event(reader)
            assert len(core.service._subscribers) == 1 and server._streams == 1
            writer.close()
            for _ in range(100):
                await asyncio.sleep(0.02)
                if server._streams == 0:
                    break
            assert server._streams == 0 and len(core.service._subscribers) == 0

    run(core, sock_dir, scenario)


def test_streams_beyond_the_limit_are_refused_and_do_not_use_up_other_slots(core, sock_dir):
    async def scenario(path, server):
        held = [await open_stream(path) for _ in range(control_module.MAX_STREAMS)]
        data = await raw(path, REQUEST)
        assert parse(data)[0] == 503
        assert parse(await raw(path, b"GET /status HTTP/1.1\r\n\r\n"))[0] == 200  # ordinary requests still work
        for _, w, _ in held:
            w.close()

    run(core, sock_dir, scenario)


def test_a_client_that_never_reads_is_dropped_without_slowing_the_core(core, sock_dir, monkeypatch):
    monkeypatch.setattr(control_module, "STREAM_WRITE_DEADLINE_SECONDS", 0.3)
    monkeypatch.setattr(control_module, "MIN_EVENT_INTERVAL_SECONDS", 0.0)

    async def scenario(path, server):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024)  # a tiny buffer that nobody empties
        sock.setblocking(False)
        await asyncio.get_running_loop().sock_connect(sock, str(path))
        await asyncio.get_running_loop().sock_sendall(sock, REQUEST)
        before = time.monotonic()
        beats = []

        async def heartbeat():
            while True:
                beats.append(time.monotonic())
                await asyncio.sleep(0.01)

        beat = asyncio.create_task(heartbeat())
        for i in range(6000):  # far more than any buffer holds, one change at a time
            core.service.simulate_ctcss(100.0 + (i % 997))
            await asyncio.sleep(0.001)
            if server._streams == 0:
                break
        for _ in range(200):
            await asyncio.sleep(0.02)
            if server._streams == 0:
                break
        beat.cancel()
        sock.close()
        assert server._streams == 0, "the stalled client was not dropped"
        gaps = [b - a for a, b in zip(beats, beats[1:])]
        assert max(gaps) < 0.25, "the event loop was held up"
        assert time.monotonic() - before < 15

    run(core, sock_dir, scenario)


def test_other_methods_on_events_are_refused(core, sock_dir):
    async def scenario(path, server):
        post = b"POST /events HTTP/1.1\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n{}"
        assert parse(await raw(path, post))[0] == 404  # no such POST path

    run(core, sock_dir, scenario)


def test_stopping_the_server_ends_open_streams(core, sock_dir):
    async def go():
        path = sock_dir / "core.sock"
        server = make_server(core, path)
        await server.start()
        reader, writer, _ = await open_stream(path)
        await next_event(reader)
        await server.stop()
        assert await asyncio.wait_for(reader.read(), 5) == b""  # the stream ended
        for _ in range(100):  # the server's own cleanup runs a moment later
            if server._streams == 0:
                break
            await asyncio.sleep(0.02)
        assert server._streams == 0 and len(core.service._subscribers) == 0
        writer.close()

    asyncio.run(go())


# -- countdowns are not news ---------------------------------------------------------------------


def _snap(**changes):
    from api.service.snapshot import StatusSnapshot

    base = dict(
        state="idle", ptt_active=False, cos_active=False, ctcss_hz=None, linked_nodes=[], last_clip=None,
        timestamp=100.0, tot_remaining=None, hang_remaining=None, id_due_in=500.0,
    )
    base.update(changes)
    return StatusSnapshot(**base)


def test_a_countdown_that_just_counts_down_is_not_news():
    assert not control_module._is_news(_snap(), _snap(timestamp=100.25, id_due_in=499.75))
    assert not control_module._is_news(_snap(), _snap(timestamp=130.0, id_due_in=470.0))


def test_a_countdown_that_jumps_or_appears_or_ends_is_news():
    assert control_module._is_news(_snap(), _snap(timestamp=100.25, id_due_in=600.0))  # the ID was sent
    assert control_module._is_news(_snap(), _snap(timestamp=101.0, id_due_in=None))
    assert control_module._is_news(_snap(), _snap(timestamp=101.0, tot_remaining=180.0))
    assert control_module._is_news(_snap(tot_remaining=10.0), _snap(timestamp=111.0, tot_remaining=10.0))  # was reset


def test_any_other_change_is_news():
    assert control_module._is_news(_snap(), _snap(cos_active=True))
    assert control_module._is_news(_snap(), _snap(last_clip="id"))
    assert control_module._is_news(_snap(), _snap(linked_nodes=["1999"]))


def test_an_idle_stream_sends_no_status_for_ticking_countdowns_but_still_heartbeats(core, sock_dir, monkeypatch):
    """Found on the Pi: the next-ID countdown made an idle core push four status events a
    second, and starved the heartbeat. Ticks alone must not."""
    monkeypatch.setattr(control_module, "HEARTBEAT_SECONDS", 0.4)
    # Start from the real clock: the controller armed its ID deadline from it when built, and
    # a fixed start can be past that deadline on a machine that booted recently (CI did).
    clock = [time.monotonic()]
    core.service._clock = lambda: clock[0]
    core.service.controller.config = dataclasses.replace(core.service.controller.config, idle_id=True)
    core.service.tick()
    assert core.service.snapshot().id_due_in is not None  # the countdown that moves on an idle repeater

    async def scenario(path, server):
        reader, writer, _ = await open_stream(path)
        await next_event(reader)  # the initial status
        names = []
        deadline = time.monotonic() + 1.3
        while time.monotonic() < deadline:
            clock[0] += 0.3  # a quarter-second bucket each time, so the service does publish
            core.service.tick()
            try:
                name, _ = await next_event(reader, timeout=0.05)
                names.append(name)
            except asyncio.TimeoutError:
                pass
            await asyncio.sleep(0.05)
        assert "status" not in names
        assert "heartbeat" in names
        writer.close()

    run(core, sock_dir, scenario)


def test_a_configuration_change_is_announced_with_a_new_version(core, sock_dir):
    async def scenario(path, server):
        reader, writer, _ = await open_stream(path)
        _, first = await next_event(reader)
        core.service.update_config(tot_duration=95)  # nothing an operator sees on the status changes
        name, second = await next_event(reader)
        assert name == "status"
        assert second["local"]["config_version"] > first["local"]["config_version"]
        writer.close()

    run(core, sock_dir, scenario)
