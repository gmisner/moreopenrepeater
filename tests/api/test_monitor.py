import asyncio
import tempfile
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from api.app import create_app
from api.assets import AudioAssetStore
from api.live_audio import LiveAudio
from api.monitor import FRAME_BLOCKS, LISTENER_QUEUE_FRAMES, AudioMonitor
from api.service import RepeaterService
from playout.renderer import ClipRenderer

BLOCK = 320


def run(coro):
    return asyncio.run(coro)


def test_frames_batch_blocks_and_route_by_source():
    async def scenario():
        monitor = AudioMonitor()
        rx_queue = monitor.subscribe("rx")
        tx_queue = monitor.subscribe("tx")
        for _ in range(FRAME_BLOCKS):
            monitor.feed(np.full(BLOCK, 0.5, dtype=np.float32), np.zeros(BLOCK, dtype=np.float32))
        await asyncio.sleep(0)
        rx = np.frombuffer(rx_queue.get_nowait(), dtype="<i2")
        tx = np.frombuffer(tx_queue.get_nowait(), dtype="<i2")
        assert len(rx) == len(tx) == BLOCK * FRAME_BLOCKS
        assert rx[0] == 16383 and tx.max() == 0

    run(scenario())


def test_nothing_buffered_without_listeners_and_slow_listeners_drop():
    async def scenario():
        monitor = AudioMonitor()
        monitor.feed(np.zeros(BLOCK, dtype=np.float32), np.zeros(BLOCK, dtype=np.float32))
        assert monitor._rx == []

        queue = monitor.subscribe("rx")
        for _ in range(FRAME_BLOCKS * (LISTENER_QUEUE_FRAMES + 5)):
            monitor.feed(np.zeros(BLOCK, dtype=np.float32), np.zeros(BLOCK, dtype=np.float32))
        await asyncio.sleep(0)
        assert queue.qsize() == LISTENER_QUEUE_FRAMES

        monitor.unsubscribe(queue)
        assert monitor.listener_count == 0

    run(scenario())


def test_audio_websocket_streams_pcm():
    tmp = Path(tempfile.mkdtemp())
    renderer = ClipRenderer(AudioAssetStore(tmp / "audio").path_for, tts=None)
    service = RepeaterService(renderer=renderer)
    live = LiveAudio(service, renderer)
    app = create_app(
        service=service, renderer=renderer, live_audio=live, start_background_tick=False, log_path=tmp / "t.log"
    )
    client = TestClient(app)
    with client.websocket_connect("/ws/audio?source=rx") as ws:
        assert ws.receive_json() == {"sample_rate": 16000, "source": "rx"}
        assert live.monitor.listener_count == 1
        for _ in range(FRAME_BLOCKS):
            live.monitor.feed(np.full(BLOCK, 0.25, dtype=np.float32), np.zeros(BLOCK, dtype=np.float32))
        frame = ws.receive_bytes()
        assert len(frame) == BLOCK * FRAME_BLOCKS * 2
    assert client.get("/api/audio/engine").json()["listeners"] == 0


def test_audio_websocket_rejects_bad_source():
    tmp = Path(tempfile.mkdtemp())
    client = TestClient(create_app(start_background_tick=False, log_path=tmp / "t.log"))
    try:
        with client.websocket_connect("/ws/audio?source=nope") as ws:
            ws.receive_json()
    except Exception:
        return
    raise AssertionError("expected the connection to be refused")
