import inspect
import tempfile
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

import audio_io.receiver as receiver_module
from api.app import create_app
from api.assets import AudioAssetStore
from api.live_audio import LiveAudio
from api.monitor import FRAME_BLOCKS
from api.monitor_receiver import MonitorReceiverService, setup_problem
from api.recordings import RecordingStore
from api.service import RepeaterService
from api.users import UserStore
from audio_io.engine import AudioEngine
from audio_io.receiver import MonitorReceiver, MonitorSettings
from controller.state_machine import IDLE, RepeaterConfig
from playout.renderer import ClipRenderer

RATE = 16000
BLOCK = RATE // 50
START = 1_800_000_000.0


class FakeInputStream:
    made = []

    def __init__(self, **kwargs):
        FakeInputStream.made.append(kwargs)
        self.sample_rate = kwargs["sample_rate"]
        self.block_size = kwargs["block_size"]
        self.dropped_input_blocks = 0

    def start(self):
        pass

    def stop(self):
        pass


class FakeDuplexStream:
    def __init__(self, sample_rate, block_size, input_queue, output_queue, device):
        self.sample_rate = sample_rate
        self.block_size = block_size
        self.dropped_input_blocks = 0
        self.starved_output_blocks = 0

    def start(self):
        pass

    def stop(self):
        pass


class ImmediateLoop:
    def call_soon_threadsafe(self, fn, *args):
        fn(*args)

    def run_in_executor(self, _executor, fn, *args):
        fn(*args)


class FakePin:
    def __init__(self, pin, output, active_low):
        self.pin, self.active_low, self.value, self.closed = pin, active_low, False, False

    def read(self):
        return self.value

    def close(self):
        self.closed = True


def tone(seconds, level=0.3):
    t = np.arange(int(RATE * seconds)) / RATE
    return (level * np.sin(2 * np.pi * 1000 * t)).astype(np.float32)


def silence(seconds):
    return np.zeros(int(RATE * seconds), dtype=np.float32)


def feed(receiver, signal):
    for start in range(0, len(signal), BLOCK):
        receiver.process_one(signal[start : start + BLOCK])


def make_receiver(**settings):
    heard, changes = [], []
    receiver = MonitorReceiver(
        MonitorSettings(sample_rate=RATE, **settings), BLOCK,
        on_audio=lambda block, open_: heard.append((block, open_)), on_squelch=changes.append,
        stream_factory=FakeInputStream,
    )
    return receiver, heard, changes


# -- the receiver -------------------------------------------------------------


def test_vox_squelch_opens_on_signal_and_mutes_the_noise_between():
    receiver, heard, changes = make_receiver(vox_threshold_db=-30, vox_hold=0.2)
    feed(receiver, silence(0.5))
    assert changes == [] and all(not block.any() for block, _ in heard)
    feed(receiver, tone(1.0))
    assert changes == [True] and receiver.squelch_open
    assert heard[-1][0].any()
    feed(receiver, silence(0.5))
    assert changes == [True, False]


def test_open_squelch_passes_everything_with_gain():
    receiver, heard, _changes = make_receiver(squelch="open", gain_db=6.0)
    feed(receiver, np.full(BLOCK, 0.1, dtype=np.float32))
    block, open_ = heard[-1]
    assert open_ and np.allclose(block, 0.1 * 10 ** (6 / 20), atol=1e-4)


def test_gpio_squelch_follows_the_pin():
    receiver, _heard, changes = make_receiver(squelch="gpio")
    feed(receiver, tone(0.1))
    assert changes == []
    receiver.set_external_cos(True)
    feed(receiver, silence(0.1))
    assert changes == [True]


def test_device_rate_is_resampled():
    receiver, heard, _changes = make_receiver(squelch="open")
    FakeInputStream.made.clear()
    original = FakeInputStream.__init__

    def at_48k(self, **kwargs):
        original(self, **kwargs)
        self.sample_rate, self.block_size = 48000, kwargs["block_size"] * 3

    FakeInputStream.__init__ = at_48k
    try:
        receiver.start()
        receiver.process_one(np.zeros(960 * 5, dtype=np.float32))
        receiver.stop()
    finally:
        FakeInputStream.__init__ = original
    assert receiver.device_sample_rate == 48000
    assert sum(len(block) for block, _ in heard) in range(BLOCK * 4, BLOCK * 6)


def test_the_receiver_cannot_reach_the_transmitter():
    """47 CFR 97.113: the monitor never gets an output stream, a processor or the controller."""
    source = inspect.getsource(receiver_module)
    for forbidden in ("AudioProcessor", "from .processor", "from .engine", "output_queue", "sd.Stream(", "OutputStream", "controller"):
        assert forbidden not in source.split('"""', 2)[2], forbidden
    FakeInputStream.made.clear()
    receiver, _heard, _changes = make_receiver()
    receiver.start()
    receiver.stop()
    assert set(FakeInputStream.made[0]) == {"sample_rate", "block_size", "input_queue", "device"}


# -- the service --------------------------------------------------------------


def make_service(**config):
    tmp = Path(tempfile.mkdtemp())
    recordings = RecordingStore(tmp / "monitor-recordings", sample_rate=RATE)
    clock = {"now": START}
    service = RepeaterService(config=RepeaterConfig(**{"monitor_enabled": True, "monitor_input_device": "USB Audio 2", **config}))
    pins = []

    def open_pin(pin, output, active_low):
        pins.append(FakePin(pin, output, active_low))
        return pins[-1]

    monitor = MonitorReceiverService(
        service, RATE, recordings,
        receiver_factory=lambda *a, **kw: MonitorReceiver(*a, stream_factory=FakeInputStream, **kw),
        open_pin=open_pin, clock=lambda: clock["now"],
    )
    monitor.attach(ImmediateLoop())
    return monitor, service, recordings, pins


def test_setup_problems():
    assert setup_problem(RepeaterConfig(monitor_enabled=True, monitor_input_device="USB 2")) is None
    assert "its own input" in setup_problem(RepeaterConfig(audio_enabled=True, audio_input_device="USB", monitor_input_device="USB"))
    assert "COS pin" in setup_problem(RepeaterConfig(
        audio_enabled=True, monitor_input_device="USB 2", cos_source="gpio", cos_gpio_pin=22, monitor_squelch="gpio", monitor_gpio_pin=22,
    ))
    assert "PTT pin" in setup_problem(RepeaterConfig(
        audio_enabled=True, monitor_input_device="USB 2", ptt_output="gpio", ptt_gpio_pin=22, monitor_squelch="gpio", monitor_gpio_pin=22,
    ))


def test_starts_restarts_and_applies_levels_live():
    monitor, service, _recordings, _pins = make_service()
    first = monitor.receiver
    assert first is not None and monitor.status()["running"]
    service.update_config(monitor_gain_db=6, monitor_vox_threshold_db=-50)
    assert monitor.receiver is first
    assert first.settings.gain_db == 6 and first.settings.vox_threshold_db == -50
    service.update_config(monitor_input_device="USB Audio 3")
    assert monitor.receiver is not first
    service.update_config(monitor_enabled=False)
    assert monitor.receiver is None and not monitor.status()["running"]


def test_refuses_the_repeaters_own_input():
    monitor, service, _recordings, _pins = make_service()
    service.update_config(audio_enabled=True, audio_input_device="USB Audio 2")
    assert monitor.receiver is None
    assert "its own input" in monitor.status()["error"]


def test_gpio_squelch_claims_and_releases_its_pin():
    monitor, service, _recordings, pins = make_service(monitor_squelch="gpio", monitor_gpio_pin=23, monitor_gpio_polarity="high")
    assert pins[0].pin == 23 and pins[0].active_low is False
    service.update_config(monitor_enabled=False)
    assert pins[0].closed


def test_records_transmissions_when_asked():
    monitor, service, recordings, _pins = make_service(monitor_vox_threshold_db=-30)
    feed(monitor.receiver, tone(1.5))
    feed(monitor.receiver, silence(1.5))
    assert recordings.list() == []  # recording is off

    service.update_config(monitor_record=True)
    feed(monitor.receiver, tone(1.5))
    feed(monitor.receiver, silence(1.5))
    [saved] = recordings.list()
    assert 1.4 <= saved.duration <= 2.5

    feed(monitor.receiver, tone(0.3))  # too short to keep
    feed(monitor.receiver, silence(1.5))
    assert len(recordings.list()) == 1


def test_monitor_audio_never_reaches_the_repeater():
    """A loud monitor signal doesn't key the repeater, open its COS, or show up
    in what the repeater transmits or receives."""
    tmp = Path(tempfile.mkdtemp())
    renderer = ClipRenderer(AudioAssetStore(tmp / "audio").path_for, tts=None, sample_rate=RATE)
    service = RepeaterService(
        config=RepeaterConfig(
            audio_enabled=True, audio_input_device="USB Audio", vox_threshold_db=-30,
            monitor_enabled=True, monitor_input_device="USB Audio 2", monitor_squelch="open",
        ),
        renderer=renderer,
    )
    live = LiveAudio(service, renderer, engine_factory=lambda *a, **kw: AudioEngine(*a, stream_factory=FakeDuplexStream, **kw))
    transmitted = []
    live.attach(ImmediateLoop())
    original = live.engine._on_audio
    live.engine._on_audio = lambda rx, tx: (transmitted.append(tx), original(rx, tx))
    monitor = MonitorReceiverService(
        service, RATE, receiver_factory=lambda *a, **kw: MonitorReceiver(*a, stream_factory=FakeInputStream, **kw),
    )
    monitor.attach(ImmediateLoop())

    loud = tone(2.0, level=0.9)
    for start in range(0, len(loud), BLOCK):
        monitor.receiver.process_one(loud[start : start + BLOCK])
        live.engine.process_one(np.zeros(BLOCK, dtype=np.float32))
    assert monitor.receiver.squelch_open
    assert service.controller.state == IDLE
    assert not live.engine.processor.cos_open and not live.engine.transmitting
    assert transmitted and not any(block.any() for block in transmitted)


# -- the dashboard ------------------------------------------------------------


def make_client(tmp_path, monkeypatch):
    monkeypatch.setenv("MOREOPENREPEATER_LOG_PATH", str(tmp_path / "t.log"))
    service = RepeaterService(config=RepeaterConfig(callsign="W1AW", monitor_name="Aviation 119.1"))
    recordings = RecordingStore(tmp_path / "recordings", sample_rate=RATE)
    monitor_recordings = RecordingStore(tmp_path / "monitor-recordings", sample_rate=RATE)
    monitor = MonitorReceiverService(service, RATE, monitor_recordings)
    app = create_app(
        service=service, start_background_tick=False, users=UserStore(), recordings=recordings,
        monitor_recordings=monitor_recordings, monitor_receiver=monitor,
    )
    return TestClient(app), recordings, monitor_recordings, monitor


def test_status_endpoint(tmp_path, monkeypatch):
    client, _recordings, _monitor_recordings, _monitor = make_client(tmp_path, monkeypatch)
    status = client.get("/api/monitor-receiver").json()
    assert status["enabled"] is False and status["running"] is False and status["name"] == "Aviation 119.1"
    saved = client.put("/api/config", json={"monitor_squelch": "gpio", "monitor_gpio_pin": 22, "monitor_gain_db": 3}).json()
    assert saved["monitor_squelch"] == "gpio" and saved["monitor_gain_db"] == 3
    assert client.put("/api/config", json={"monitor_gpio_pin": 40}).status_code == 422


def test_monitor_recordings_are_kept_apart(tmp_path, monkeypatch):
    client, recordings, monitor_recordings, _monitor = make_client(tmp_path, monkeypatch)
    repeater = recordings.save(np.zeros(RATE, dtype=np.float32), START)
    heard = monitor_recordings.save(np.zeros(RATE, dtype=np.float32), START + 60)

    assert [r["id"] for r in client.get("/api/recordings").json()] == [repeater.id]
    assert [r["id"] for r in client.get("/api/recordings", params={"source": "monitor"}).json()] == [heard.id]
    assert client.get(f"/api/recordings/{heard.id}/audio").status_code == 404
    assert client.get(f"/api/recordings/{heard.id}/audio", params={"source": "monitor"}).status_code == 200
    assert client.delete(f"/api/recordings/{heard.id}", params={"source": "monitor"}).json() == {"deleted": heard.id}
    assert monitor_recordings.list() == [] and len(recordings.list()) == 1
    assert client.get("/api/recordings", params={"source": "nope"}).status_code == 422


def test_dashboard_can_listen_to_the_monitor(tmp_path, monkeypatch):
    client, _recordings, _monitor_recordings, monitor = make_client(tmp_path, monkeypatch)
    with client.websocket_connect("/ws/audio?source=monitor") as ws:
        assert ws.receive_json() == {"sample_rate": RATE, "source": "monitor"}
        assert monitor.monitor.listener_count == 1
        for _ in range(FRAME_BLOCKS):
            monitor.monitor.feed(np.full(BLOCK, 0.25, dtype=np.float32), np.full(BLOCK, 0.25, dtype=np.float32))
        assert len(ws.receive_bytes()) == BLOCK * FRAME_BLOCKS * 2
    assert monitor.monitor.listener_count == 0
