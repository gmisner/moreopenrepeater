import asyncio
import importlib

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from api.app import create_app
from api.monitor import AudioMonitor
from api.persistence import StateStore
from api.service import RepeaterService
from api.stream import Streamer, ffmpeg_command
from api.users import UserStore
from controller.state_machine import RepeaterConfig

stream_module = importlib.import_module("api.stream")


def make_client(tmp_path, monkeypatch, **config):
    monkeypatch.setenv("MOREOPENREPEATER_LOG_PATH", str(tmp_path / "t.log"))
    service = RepeaterService(config=RepeaterConfig(callsign="W1AW", **config))
    app = create_app(service=service, start_background_tick=False, users=UserStore(), stream_store=StateStore(tmp_path / "stream.json"))
    return TestClient(app), service


def test_public_page_is_off_by_default(tmp_path, monkeypatch):
    client, _service = make_client(tmp_path, monkeypatch)
    assert client.get("/api/public/status").status_code == 404
    assert client.get("/listen").status_code == 404
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/public/audio") as ws:
            ws.receive_json()


def test_public_page_shows_status_without_signing_in(tmp_path, monkeypatch):
    client, _service = make_client(tmp_path, monkeypatch, public_page_enabled=True, public_page_text="146.940 -600 PL 100")
    client.post("/api/users", json={"username": "alice", "password": "password1", "role": "admin"})
    assert client.get("/api/status").status_code == 401

    status = client.get("/api/public/status").json()
    assert status == {
        "callsign": "W1AW", "text": "146.940 -600 PL 100", "on_air": False, "receiving": False,
        "net": None, "audio": True, "listeners": 0, "max_listeners": 20,
    }
    assert "Listen live" in client.get("/listen").text


def test_public_audio_is_capped_per_address(tmp_path, monkeypatch):
    client, _service = make_client(tmp_path, monkeypatch, public_page_enabled=True)
    with client.websocket_connect("/ws/public/audio") as a, client.websocket_connect("/ws/public/audio") as b:
        with client.websocket_connect("/ws/public/audio") as c:
            assert a.receive_json()["sample_rate"] and b.receive_json() and c.receive_json()
            assert client.get("/api/public/status").json()["listeners"] == 3
            with pytest.raises(WebSocketDisconnect) as closed:
                with client.websocket_connect("/ws/public/audio") as d:
                    d.receive_json()
            assert closed.value.code == 1013


def test_public_audio_can_be_off_while_the_page_is_on(tmp_path, monkeypatch):
    client, _service = make_client(tmp_path, monkeypatch, public_page_enabled=True, public_page_audio=False)
    assert client.get("/api/public/status").json()["audio"] is False
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/public/audio") as ws:
            ws.receive_json()


def test_stream_settings_keep_the_password_private(tmp_path, monkeypatch):
    client, _service = make_client(tmp_path, monkeypatch)
    body = {"enabled": False, "host": "audio9.broadcastify.com", "port": 80, "mount": "abc123", "password": "hunter22"}
    response = client.put("/api/stream", json=body).json()
    assert response["password_set"] is True and "password" not in response
    assert response["status"]["state"] == "off"

    client.put("/api/stream", json={**body, "password": "", "mount": "xyz"})
    saved = StateStore(tmp_path / "stream.json").load()
    assert saved["password"] == "hunter22" and saved["mount"] == "xyz"
    assert "hunter22" not in client.get("/api/stream").text
    assert client.put("/api/stream", json={**body, "host": "bad host; rm"}).status_code == 422


def test_ffmpeg_command():
    settings = {**stream_module.DEFAULT_SETTINGS, "host": "audio9.broadcastify.com", "mount": "abc", "password": "pw", "name": "W1AW"}
    command = ffmpeg_command("/usr/bin/ffmpeg", settings, 16000)
    assert command[-1] == "icecast://source@audio9.broadcastify.com:80/abc"
    assert command[command.index("-password") + 1] == "pw"
    assert command[command.index("-b:a") + 1] == "16k"
    assert command[command.index("-ice_name") + 1] == "W1AW"
    assert "-legacy_icecast" not in command


class FakeStdin:
    def __init__(self, process, fail_after):
        self.process = process
        self.fail_after = fail_after
        self.frames = []

    def write(self, data):
        if self.process.returncode is not None:
            raise BrokenPipeError
        self.frames.append(data)
        if self.fail_after is not None and len(self.frames) >= self.fail_after:
            self.process.returncode = 1

    async def drain(self):
        await asyncio.sleep(0)


class FakeStderr:
    def __init__(self, text):
        self.text = text

    async def read(self):
        return self.text


class FakeProcess:
    def __init__(self, fail_after=None, stderr=b""):
        self.returncode = None
        self.stdin = FakeStdin(self, fail_after)
        self.stderr = FakeStderr(stderr)

    def terminate(self):
        self.returncode = -15

    def kill(self):
        self.returncode = -9

    async def wait(self):
        return self.returncode


@pytest.fixture
def fast(monkeypatch):
    monkeypatch.setattr(stream_module, "SILENCE_SECONDS", 0.01)
    monkeypatch.setattr(stream_module, "STREAMING_AFTER_SECONDS", 0.0)


SETTINGS = {"enabled": True, "host": "audio9.broadcastify.com", "mount": "abc", "password": "s3cret"}


async def until(predicate, steps=200):
    for _ in range(steps):
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("timed out")


def test_streamer_streams_and_stops(fast):
    processes = []

    async def spawn(*command, **kwargs):
        processes.append((command, FakeProcess()))
        return processes[-1][1]

    async def go():
        streamer = Streamer(AudioMonitor(), None, lambda: 16000, which=lambda name: "/usr/bin/ffmpeg", spawn=spawn)
        task = asyncio.create_task(streamer.run())
        await until(lambda: streamer.status["state"] == "off")
        streamer.update(SETTINGS)
        await until(lambda: streamer.status["state"] == "streaming")
        process = processes[0][1]
        await until(lambda: len(process.stdin.frames) > 2)
        assert process.stdin.frames[0] == bytes(int(16000 * 0.01) * 2)  # silence while nothing's on the air
        streamer.update({"enabled": False})
        await until(lambda: streamer.status["state"] == "off")
        assert process.returncode == -15
        task.cancel()

    asyncio.run(go())


def test_streamer_retries_and_hides_the_password(fast):
    async def spawn(*command, **kwargs):
        return FakeProcess(fail_after=1, stderr=b"icecast://source:s3cret@host: 401 Unauthorized\n")

    async def go():
        streamer = Streamer(AudioMonitor(), None, lambda: 16000, which=lambda name: "/usr/bin/ffmpeg", spawn=spawn)
        streamer.update(SETTINGS)
        task = asyncio.create_task(streamer.run())
        await until(lambda: streamer.status["state"] == "retrying")
        assert "401 Unauthorized" in streamer.status["detail"] and "s3cret" not in streamer.status["detail"]
        assert "Trying again in 5 s" in streamer.status["detail"]
        task.cancel()

    asyncio.run(go())


def test_streamer_without_ffmpeg_or_settings():
    async def go():
        streamer = Streamer(AudioMonitor(), None, lambda: 16000, which=lambda name: None)
        streamer.update({"enabled": True})
        task = asyncio.create_task(streamer.run())
        await until(lambda: streamer.status["state"] == "error")
        assert "server, mount and password" in streamer.status["detail"]
        streamer.update(SETTINGS)
        await until(lambda: "ffmpeg isn't installed" in streamer.status["detail"])
        task.cancel()

    asyncio.run(go())
