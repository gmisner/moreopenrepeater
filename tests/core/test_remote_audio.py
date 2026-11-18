"""The dashboard's stand-in for the live audio monitor, fed by the core over the socket
(Stage 3 slice 7c-9, #229). A real control server and a real `RemoteAudioMonitor`."""
import asyncio
import json
import struct

import numpy as np
import pytest
from control_helpers import make_server

from api import remote_audio as remote_module
from api.monitor import LISTENER_QUEUE_FRAMES
from api.remote_audio import RemoteAudioMonitor


@pytest.fixture(autouse=True)
def quick_retries(monkeypatch):
    monkeypatch.setattr(remote_module, "RETRY_DELAYS", (0.05, 0.1))


def feed(core, rx, tx, device="live_audio", count=5, size=160):
    monitor = getattr(core.runtime, device).monitor
    for _ in range(count):
        monitor.feed(np.full(size, rx, dtype=np.float32), np.full(size, tx, dtype=np.float32))


async def until(condition, timeout=5.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not condition():
        assert asyncio.get_running_loop().time() < deadline, "timed out"
        await asyncio.sleep(0.01)


def run(core, sock_dir, scenario):
    async def go():
        path = sock_dir / "core.sock"
        server = make_server(core, path)
        await server.start()
        try:
            return await scenario(path, server)
        finally:
            await server.stop()

    return asyncio.run(go())


async def next_pcm(queue, timeout=5):
    return np.frombuffer(await asyncio.wait_for(queue.get(), timeout), dtype="<i2")


def test_a_listener_hears_what_the_core_feeds_its_monitor(core, sock_dir):
    async def scenario(path, server):
        monitor = RemoteAudioMonitor(path, "live")
        queue = monitor.subscribe("rx")
        await until(lambda: core.runtime.live_audio.monitor.listener_count == 1)
        feed(core, 0.5, -0.5)
        pcm = await next_pcm(queue)
        monitor.unsubscribe(queue)
        return monitor, pcm

    monitor, pcm = run(core, sock_dir, scenario)
    assert set(pcm.tolist()) == {16383}
    assert monitor.sample_rate == core.runtime.renderer.sample_rate  # what the core said, not a guess


def test_listeners_of_one_source_share_one_stream_and_each_source_has_its_own(core, sock_dir):
    async def scenario(path, server):
        monitor = RemoteAudioMonitor(path, "live")
        a, b = monitor.subscribe("rx"), monitor.subscribe("rx")
        c = monitor.subscribe("tx")
        await until(lambda: core.runtime.live_audio.monitor.listener_count == 2)
        assert server._audio_streams == 2 and monitor.listener_count == 3  # two upstream, three listeners
        feed(core, 0.5, -0.5)
        heard = [await next_pcm(q) for q in (a, b, c)]
        for q in (a, b, c):
            monitor.unsubscribe(q)
        return heard

    heard = run(core, sock_dir, scenario)
    assert [set(h.tolist()) for h in heard] == [{16383}, {16383}, {-16383}]


def test_the_stream_closes_when_the_last_listener_leaves(core, sock_dir):
    async def scenario(path, server):
        monitor = RemoteAudioMonitor(path, "live")
        a, b = monitor.subscribe("rx"), monitor.subscribe("rx")
        await until(lambda: server._audio_streams == 1)
        monitor.unsubscribe(a)
        await asyncio.sleep(0.1)
        assert server._audio_streams == 1  # one is still listening
        monitor.unsubscribe(b)
        await until(lambda: server._audio_streams == 0 and core.runtime.live_audio.monitor.listener_count == 0)

    run(core, sock_dir, scenario)


def test_a_listener_that_falls_behind_loses_audio_and_the_others_do_not(core, sock_dir):
    async def scenario(path, server):
        monitor = RemoteAudioMonitor(path, "live")
        slow, quick = monitor.subscribe("rx"), monitor.subscribe("rx")
        await until(lambda: core.runtime.live_audio.monitor.listener_count == 1)
        heard = 0
        for _ in range(LISTENER_QUEUE_FRAMES + 15):  # more than the slow listener's queue holds
            feed(core, 0.5, 0.5)
            await next_pcm(quick)
            heard += 1
        size = slow.qsize()
        monitor.unsubscribe(slow)
        monitor.unsubscribe(quick)
        return heard, size

    heard, size = run(core, sock_dir, scenario)
    assert heard == LISTENER_QUEUE_FRAMES + 15 and size <= LISTENER_QUEUE_FRAMES


def test_with_the_core_away_it_keeps_trying_and_the_audio_starts_when_it_is_back(core, sock_dir):
    async def go():
        path = sock_dir / "core.sock"
        monitor = RemoteAudioMonitor(path, "live")
        queue = monitor.subscribe("rx")  # nothing is listening on the socket yet
        await asyncio.sleep(0.3)
        server = make_server(core, path)
        await server.start()
        try:
            await until(lambda: core.runtime.live_audio.monitor.listener_count == 1)
            feed(core, 0.5, 0.5)
            return await next_pcm(queue)
        finally:
            monitor.unsubscribe(queue)
            await server.stop()

    assert set(asyncio.run(go()).tolist()) == {16383}


def test_a_core_that_restarts_mid_listen_is_followed(core, sock_dir):
    async def go():
        path = sock_dir / "core.sock"
        monitor = RemoteAudioMonitor(path, "live")
        server = make_server(core, path)
        await server.start()
        queue = monitor.subscribe("rx")
        await until(lambda: core.runtime.live_audio.monitor.listener_count == 1)
        await server.stop()
        await until(lambda: core.runtime.live_audio.monitor.listener_count == 0)
        server = make_server(core, path)
        await server.start()
        try:
            await until(lambda: core.runtime.live_audio.monitor.listener_count == 1)
            feed(core, 0.25, 0.25)
            return await next_pcm(queue)
        finally:
            monitor.unsubscribe(queue)
            await server.stop()

    assert set(asyncio.run(go()).tolist()) == {8191}


def test_a_garbled_stream_is_dropped_and_tried_again_not_trusted(sock_dir):
    connections = []

    async def go():
        async def serve(reader, writer):
            connections.append(1)
            await reader.readuntil(b"\r\n\r\n")
            writer.write(b"HTTP/1.1 200 OK\r\nConnection: close\r\n\r\n")
            header = json.dumps({"sample_rate": 16000}).encode()
            writer.write(struct.pack(">I", len(header)) + header)
            writer.write(struct.pack(">I", 0xFFFFFFFF))  # a frame that claims to be four gigabytes
            await writer.drain()
            await reader.read()  # and holds the line open: only a client that rejects the frame comes back
            writer.close()

        path = sock_dir / "fake.sock"
        server = await asyncio.start_unix_server(serve, str(path))
        monitor = RemoteAudioMonitor(path, "live")
        queue = monitor.subscribe("rx")
        try:
            await until(lambda: len(connections) >= 3)  # it went back for more each time
            return queue.qsize()
        finally:
            monitor.unsubscribe(queue)
            server.close()
            await server.wait_closed()

    assert asyncio.run(go()) == 0  # nothing the garbled stream held was passed on as audio
