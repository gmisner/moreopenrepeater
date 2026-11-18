"""The API leaves a mailbox message through the core (Stage 3 slice 7c-10, #229): a real control server
and a real `RemoteCore`. The core records the transmission; the API receives it."""
import asyncio
import threading

import numpy as np
import pytest
from control_helpers import make_server

from api import remote_core as remote_module
from api.remote_core import RemoteCore
from playout.wav import encode_wav


@pytest.fixture(autouse=True)
def quick_retries(monkeypatch):
    monkeypatch.setattr(remote_module, "RETRY_DELAYS", (0.05, 0.1))
    monkeypatch.setattr(remote_module, "HARDWARE_POLL_SECONDS", 0.1)


async def until(condition, timeout=5.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not condition():
        assert asyncio.get_running_loop().time() < deadline, "timed out"
        await asyncio.sleep(0.02)


@pytest.fixture
def engine(core, monkeypatch):
    """The core's engine: running, and recording what it is asked to arm."""
    armed = []
    real = core.runtime.live_audio.status
    monkeypatch.setattr(core.runtime.live_audio, "status", lambda: {**real(), "running": True})
    monkeypatch.setattr(core.runtime.live_audio, "arm_capture", lambda seconds, done: armed.append((seconds, done)) or True)
    return armed


def run(core, sock_dir, tmp_path, scenario):
    async def go():
        path = sock_dir / "core.sock"
        server = make_server(core, path, capture_dir=tmp_path / "captures")
        await server.start()
        remote = RemoteCore(path)
        remote.start()
        try:
            await until(lambda: remote.connected and server._macro_clients)
            await until(lambda: remote._hardware_at is not None)
            return await scenario(remote, server)
        finally:
            await remote.stop()
            await server.stop()

    return asyncio.run(go())


def finish(done, samples, at):
    worker = threading.Thread(target=done, args=(samples, at))  # the engine's pool thread
    worker.start()
    worker.join(5)


def tone(core, seconds=1.0):
    rate = core.runtime.renderer.sample_rate
    return (np.sin(np.linspace(0, 600, int(rate * seconds))) * 0.5).astype(np.float32)


def test_the_message_the_core_records_reaches_the_api_s_callback_and_the_file_goes(core, sock_dir, tmp_path, engine):
    received = []
    sent = tone(core)

    async def scenario(remote, server):
        assert remote.audio_output.arm_capture(45.0, lambda samples, at: received.append((samples, at, threading.current_thread())))
        await until(lambda: engine)  # the core was asked to record the next transmission
        assert engine[0][0] == 45.0
        finish(engine[0][1], sent, 1700000000.5)
        await until(lambda: received)
        await asyncio.sleep(0.1)

    run(core, sock_dir, tmp_path, scenario)
    samples, at, thread = received[0]
    assert at == 1700000000.5 and len(samples) == len(sent) and samples.dtype == np.float32
    assert np.max(np.abs(samples - sent)) < 1e-3  # a 16-bit file, so not bit for bit
    assert thread is not threading.main_thread()  # as in process: not on the event loop
    assert list((tmp_path / "captures").glob("*")) == []  # taken, so gone


def test_the_caller_is_told_at_once_when_the_core_is_not_running_live_audio(core, sock_dir, tmp_path, engine, monkeypatch):
    real = core.runtime.live_audio.status
    monkeypatch.setattr(core.runtime.live_audio, "status", lambda: {**real(), "running": False})

    async def scenario(remote, server):
        await until(lambda: remote.hardware_status("live_audio").get("running") is False, timeout=8.0)
        return remote.audio_output.arm_capture(30.0, lambda samples, at: None)

    assert run(core, sock_dir, tmp_path, scenario) is False and engine == []  # nothing was asked of the core


def test_with_the_core_away_arming_says_no_and_does_not_raise(sock_dir):
    async def go():
        remote = RemoteCore(sock_dir / "nothing.sock")
        return remote.audio_output.arm_capture(30.0, lambda samples, at: None)

    assert asyncio.run(go()) is False


def test_a_core_that_refuses_after_all_is_spoken_about_and_nothing_is_waited_for(core, sock_dir, tmp_path, engine, monkeypatch):
    monkeypatch.setattr(core.runtime.live_audio, "arm_capture", lambda seconds, done: False)  # it stopped in between
    said, received = [], []

    async def scenario(remote, server):
        monkeypatch.setattr(remote, "speak", lambda clip, on_linked_radio=False: said.append(clip))
        assert remote.audio_output.arm_capture(30.0, lambda samples, at: received.append(at))
        await until(lambda: said)
        assert remote._capture_waiting is None

    run(core, sock_dir, tmp_path, scenario)
    assert said == ["tts:Messages need live audio, which is not running."] and received == []


def test_a_recording_with_no_one_waiting_is_dropped_and_deleted(core, sock_dir, tmp_path, engine, caplog):
    import logging

    caplog.set_level(logging.WARNING)

    async def scenario(remote, server):
        path = tmp_path / "captures" / "capture-0123456789abcdef.wav"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(encode_wav(tone(core, 0.2), core.runtime.renderer.sample_rate))
        server._hand_over_capture(path, 5.0)
        await until(lambda: not path.exists())

    run(core, sock_dir, tmp_path, scenario)
    assert "no one was waiting" in caplog.text


def test_the_api_only_opens_files_that_are_the_core_s_recordings(core, sock_dir, tmp_path, engine):
    received = []

    async def scenario(remote, server):
        remote.audio_output.arm_capture(30.0, lambda samples, at: received.append(at))
        decoys = [
            tmp_path / "elsewhere" / "capture-0123456789abcdef.wav",  # not in a captures folder
            tmp_path / "captures" / "passwd.wav",  # not named like a recording
            tmp_path / "captures" / "capture-0123456789abcdef.wav.bak",
        ]
        for decoy in decoys:
            decoy.parent.mkdir(parents=True, exist_ok=True)
            decoy.write_bytes(encode_wav(tone(core, 0.2), core.runtime.renderer.sample_rate))
            assert server._api_action("capture", [str(decoy), 1.0])
        await asyncio.sleep(0.4)
        return decoys

    decoys = run(core, sock_dir, tmp_path, scenario)
    assert received == [] and all(d.exists() for d in decoys)  # neither read nor deleted


def test_a_second_arming_replaces_the_first(core, sock_dir, tmp_path, engine):
    first, second = [], []

    async def scenario(remote, server):
        remote.audio_output.arm_capture(30.0, lambda samples, at: first.append(at))
        remote.audio_output.arm_capture(20.0, lambda samples, at: second.append(at))
        await until(lambda: len(engine) == 2)
        finish(engine[1][1], tone(core), 9.0)  # the engine keeps one slot: the newer arm
        await until(lambda: second)

    run(core, sock_dir, tmp_path, scenario)
    assert first == [] and second == [9.0]
