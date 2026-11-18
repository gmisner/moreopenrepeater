"""Recording the next transmission for the API's mailbox (Stage 3 slice 7c-10, #229). The core records
(it is the one that can keep a transmission from being repeated) and hands the audio over as a file."""
import asyncio
import threading
import wave

import numpy as np
import pytest
from control_helpers import make_server
from pydantic import ValidationError
from test_control_command import make_command, post_command
from test_events_stream import next_event
from test_macro_actions_over_socket import open_stream

from contracts import CoreCommand


def run(core, sock_dir, scenario, capture_dir, **kwargs):
    async def go():
        path = sock_dir / "core.sock"
        server = make_server(core, path, capture_dir=capture_dir, **kwargs)
        await server.start()
        try:
            return await scenario(path, server)
        finally:
            await server.stop()

    return asyncio.run(go())


@pytest.fixture
def armed(core, monkeypatch):
    """The engine's `arm_capture`, recorded: what it was asked for and the callback it was given."""
    calls = []
    monkeypatch.setattr(core.runtime.live_audio, "arm_capture", lambda seconds, done: calls.append((seconds, done)) or True)
    return calls


def finish_on_a_worker(done, samples, at):
    """The engine calls the callback on a pool thread when the transmission ends."""
    worker = threading.Thread(target=done, args=(samples, at))
    worker.start()
    worker.join(5)


async def capture_event(reader):
    while True:
        name, data = await next_event(reader)
        if name == "macro" and data["family"] == "capture":
            return data


def test_arming_asks_the_engine_to_record_the_next_transmission(core, sock_dir, armed, tmp_path):
    async def scenario(path, server):
        return (await post_command(path, make_command("arm_capture", max_seconds=45.0)))[1]

    answer = run(core, sock_dir, scenario, tmp_path / "captures")
    assert answer["status"] == "applied" and [seconds for seconds, _ in armed] == [45.0]


def test_the_core_says_so_when_it_cannot_record(core, sock_dir, monkeypatch, tmp_path):
    monkeypatch.setattr(core.runtime.live_audio, "arm_capture", lambda seconds, done: False)

    async def scenario(path, server):
        return (await post_command(path, make_command("arm_capture", max_seconds=30.0)))[1]

    answer = run(core, sock_dir, scenario, tmp_path / "captures")
    assert answer["status"] == "rejected" and answer["error"]["code"] == "command.hardware_refused"
    assert "not running" in answer["error"]["message"]


def test_a_core_with_nowhere_to_put_a_recording_refuses(core, sock_dir, armed):
    async def scenario(path, server):
        return (await post_command(path, make_command("arm_capture", max_seconds=30.0)))[1]

    answer = run(core, sock_dir, scenario, None)
    assert answer["status"] == "rejected" and armed == []


@pytest.mark.parametrize("seconds", [0, -5, 0.5, 601, float("nan"), float("inf"), "30", True, None])
def test_a_length_that_makes_no_sense_never_gets_that_far(seconds):
    with pytest.raises(ValidationError):
        make_command("arm_capture", max_seconds=seconds)


def test_a_finished_recording_reaches_the_client_that_handles_it_as_a_file(core, sock_dir, armed, tmp_path):
    captures = tmp_path / "captures"
    rate = core.runtime.renderer.sample_rate
    samples = (np.sin(np.linspace(0, 400, rate)) * 0.5).astype(np.float32)  # a second of tone

    async def scenario(path, server):
        reader, writer = await open_stream(path, "capture")
        await post_command(path, make_command("arm_capture", max_seconds=30.0))
        finish_on_a_worker(armed[0][1], samples, 1234.5)
        event = await asyncio.wait_for(capture_event(reader), 5)
        writer.close()
        return event

    event = run(core, sock_dir, scenario, captures)
    filename, started_at = event["args"]
    assert started_at == 1234.5
    assert filename.startswith(str(captures)) and filename.endswith(".wav")
    with wave.open(filename) as wav:
        assert wav.getframerate() == rate and wav.getnchannels() == 1 and wav.getnframes() == len(samples)


def test_with_no_one_to_take_it_the_recording_is_not_kept(core, sock_dir, armed, tmp_path):
    captures = tmp_path / "captures"

    async def scenario(path, server):
        await post_command(path, make_command("arm_capture", max_seconds=30.0))
        finish_on_a_worker(armed[0][1], np.zeros(1600, dtype=np.float32), 1.0)
        await asyncio.sleep(0.3)

    run(core, sock_dir, scenario, captures)
    assert list(captures.glob("*")) == []  # a message nobody can be told about is not left lying around


def test_a_client_that_handles_something_else_does_not_get_it(core, sock_dir, armed, tmp_path):
    captures = tmp_path / "captures"

    async def scenario(path, server):
        reader, writer = await open_stream(path, "sensor")
        await post_command(path, make_command("arm_capture", max_seconds=30.0))
        finish_on_a_worker(armed[0][1], np.zeros(1600, dtype=np.float32), 1.0)
        await asyncio.sleep(0.3)
        writer.close()

    run(core, sock_dir, scenario, captures)
    assert list(captures.glob("*")) == []


def test_recordings_left_over_from_before_a_restart_are_cleared(core, sock_dir, tmp_path):
    captures = tmp_path / "captures"
    captures.mkdir()
    (captures / "capture-old.wav").write_bytes(b"RIFF")
    (captures / "notes.txt").write_text("not ours")

    async def scenario(path, server):
        return None

    run(core, sock_dir, scenario, captures)
    assert [p.name for p in captures.iterdir()] == ["notes.txt"]  # only what the core made, only the wav files


def test_the_command_is_one_the_contract_knows_as_hardware():
    from contracts import HARDWARE, CommandName

    assert CommandName.ARM_CAPTURE in HARDWARE
    assert isinstance(make_command("arm_capture", max_seconds=30.0), CoreCommand)
