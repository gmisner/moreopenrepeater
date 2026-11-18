"""The core's live audio on the control socket (Stage 3 slice 7c-9, #229): what the receiver hears and
what is transmitted, as 16-bit PCM frames for a dashboard that runs apart from the core."""
import asyncio
import json
import struct

import numpy as np
import pytest
from control_helpers import get, make_server

from core import control as control_module


async def open_audio(path, query="device=live&source=rx"):
    reader, writer = await asyncio.open_unix_connection(str(path))
    writer.write(f"GET /audio?{query} HTTP/1.1\r\nHost: core\r\n\r\n".encode())
    await writer.drain()
    head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
    return reader, writer, head


async def frame(reader, timeout=5):
    size = struct.unpack(">I", await asyncio.wait_for(reader.readexactly(4), timeout))[0]
    return await asyncio.wait_for(reader.readexactly(size), timeout)


def blocks(core, value_rx, value_tx, device="live_audio", count=5, size=160):
    """Feed the monitor the way the audio thread does: `count` blocks make one frame."""
    monitor = getattr(core.runtime, device).monitor
    for _ in range(count):
        monitor.feed(np.full(size, value_rx, dtype=np.float32), np.full(size, value_tx, dtype=np.float32))


def run(core, sock_dir, scenario, **kwargs):
    async def go():
        path = sock_dir / "core.sock"
        server = make_server(core, path, **kwargs)
        await server.start()
        try:
            return await scenario(path, server)
        finally:
            await server.stop()

    return asyncio.run(go())


async def until(condition, timeout=5.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not condition():
        assert asyncio.get_running_loop().time() < deadline, "timed out"
        await asyncio.sleep(0.01)


def test_a_listener_gets_the_sample_rate_and_then_the_receiver_s_audio(core, sock_dir):
    async def scenario(path, server):
        reader, writer, head = await open_audio(path, "device=live&source=rx")
        header = json.loads(await frame(reader))
        await until(lambda: core.runtime.live_audio.monitor.listener_count == 1)
        blocks(core, 0.5, -0.5)
        pcm = np.frombuffer(await frame(reader), dtype="<i2")
        writer.close()
        return head, header, pcm

    head, header, pcm = run(core, sock_dir, scenario)
    assert head.startswith(b"HTTP/1.1 200") and b"application/octet-stream" in head
    assert header == {"sample_rate": core.runtime.renderer.sample_rate, "device": "live", "source": "rx"}
    assert len(pcm) == 5 * 160 and set(pcm.tolist()) == {16383}  # 0.5 of full scale, what it heard


def test_the_transmit_side_is_a_different_stream(core, sock_dir):
    async def scenario(path, server):
        reader, writer, _ = await open_audio(path, "device=live&source=tx")
        await frame(reader)
        await until(lambda: core.runtime.live_audio.monitor.listener_count == 1)
        blocks(core, 0.5, -0.5)
        pcm = np.frombuffer(await frame(reader), dtype="<i2")
        writer.close()
        return pcm

    assert set(run(core, sock_dir, scenario).tolist()) == {-16383}


def test_the_monitor_receiver_is_its_own_device(core, sock_dir):
    async def scenario(path, server):
        reader, writer, _ = await open_audio(path, "device=monitor&source=rx")
        header = json.loads(await frame(reader))
        await until(lambda: core.runtime.monitor_receiver.monitor.listener_count == 1)
        assert core.runtime.live_audio.monitor.listener_count == 0  # not the repeater's audio
        blocks(core, 0.25, 0.25, device="monitor_receiver")
        pcm = np.frombuffer(await frame(reader), dtype="<i2")
        writer.close()
        return header, pcm

    header, pcm = run(core, sock_dir, scenario)
    assert header["device"] == "monitor" and set(pcm.tolist()) == {8191}


def test_a_listener_who_hangs_up_is_let_go(core, sock_dir):
    async def scenario(path, server):
        reader, writer, _ = await open_audio(path)
        await frame(reader)
        await until(lambda: server._audio_streams == 1)
        writer.close()
        await until(lambda: server._audio_streams == 0 and core.runtime.live_audio.monitor.listener_count == 0)

    run(core, sock_dir, scenario)


def test_there_is_a_limit_on_listeners_and_the_next_is_told_so(core, sock_dir, monkeypatch):
    monkeypatch.setattr(control_module, "MAX_AUDIO_STREAMS", 2)

    async def scenario(path, server):
        held = [await open_audio(path) for _ in range(2)]
        await until(lambda: server._audio_streams == 2)
        _, writer, head = await open_audio(path)
        writer.close()
        for _, w, _ in held:
            w.close()
        return head

    assert run(core, sock_dir, scenario).startswith(b"HTTP/1.1 503")


@pytest.mark.parametrize(
    "query",
    ["device=nope&source=rx", "device=live&source=nope", "device=monitor&source=tx", "source="],
)
def test_bad_requests_are_refused_and_the_core_carries_on(core, sock_dir, query):
    async def scenario(path, server):
        _, writer, head = await open_audio(path, query)
        writer.close()
        return head, await get(path, "/status")

    head, (alive, _) = run(core, sock_dir, scenario)
    assert head.startswith(b"HTTP/1.1 400") and alive == 200


def test_audio_is_only_read(core, sock_dir):
    from control_helpers import parse, raw

    async def scenario(path, server):
        return parse(await raw(path, b"POST /audio HTTP/1.1\r\nHost: core\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n{}"))

    status, body = run(core, sock_dir, scenario)
    assert status == 405 and "GET" in body["error"]


def test_a_listener_that_never_reads_is_dropped_without_stopping_the_others(core, sock_dir, monkeypatch):
    monkeypatch.setattr(control_module, "STREAM_WRITE_DEADLINE_SECONDS", 0.3)

    async def scenario(path, server):
        import socket

        stuck = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        stuck.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024)  # a tiny buffer nobody empties
        stuck.setblocking(False)
        await asyncio.get_running_loop().sock_connect(stuck, str(path))
        await asyncio.get_running_loop().sock_sendall(stuck, b"GET /audio?device=live&source=rx HTTP/1.1\r\nHost: core\r\n\r\n")
        reader, writer, _ = await open_audio(path)
        await frame(reader)
        await until(lambda: server._audio_streams == 2)
        for _ in range(400):  # far more audio than one buffer holds
            blocks(core, 0.1, 0.1, count=5, size=1600)
            await asyncio.sleep(0.005)
            if server._audio_streams == 1:
                break
        assert server._audio_streams == 1, "the listener that never reads was not dropped"
        blocks(core, 0.5, 0.5)
        pcm = np.frombuffer(await frame(reader), dtype="<i2")
        stuck.close()
        writer.close()
        return pcm

    assert len(run(core, sock_dir, scenario)) > 0  # the one that reads still hears
