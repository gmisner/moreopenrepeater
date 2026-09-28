import tempfile
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from api.app import create_app
from api.assets import AudioAssetStore
from api.live_audio import LiveAudio
from api.recordings import RecordingStore
from api.service import RepeaterService
from audio_io.engine import AudioEngine
from controller.events import RunAction
from controller.state_machine import RepeaterConfig
from playout.renderer import ClipRenderer

RATE = 16000
BLOCK = RATE // 50


class FakeStream:
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


def make_live(**config):
    tmp = Path(tempfile.mkdtemp())
    recordings = RecordingStore(tmp / "recordings", sample_rate=RATE)
    renderer = ClipRenderer(
        AudioAssetStore(tmp / "audio").path_for, tts=None, sample_rate=RATE, recording_path=recordings.path_for
    )
    clock = {"now": 1_800_000_000.0}
    service = RepeaterService(
        config=RepeaterConfig(audio_enabled=True, vox_threshold_db=-30, vox_hold=0.1, **config),
        clock=lambda: clock["now"],
        renderer=renderer,
    )
    live = LiveAudio(
        service,
        renderer,
        engine_factory=lambda *a, **kw: AudioEngine(*a, stream_factory=FakeStream, **kw),
        recordings=recordings,
        clock=lambda: clock["now"],
    )
    live.attach(ImmediateLoop())
    return live, service, recordings, clock


def transmission(seconds, level=0.3):
    t = np.arange(int(RATE * seconds)) / RATE
    voice = (level * np.sin(2 * np.pi * 1000 * t)).astype(np.float32)
    return np.concatenate([voice, np.zeros(RATE // 2, dtype=np.float32)])


def feed(live, signal):
    for start in range(0, len(signal), BLOCK):
        live.engine.process_one(signal[start : start + BLOCK])


def settle(service, clock):
    """Let the courtesy tone and hang time run out."""
    for _ in range(3):
        clock["now"] += 5
        service.tick()


def test_store_saves_lists_newest_first_and_prunes(tmp_path):
    store = RecordingStore(tmp_path, sample_rate=RATE)
    store.save(np.zeros(RATE, dtype=np.float32), started_at=1_800_000_000.0)
    newer = store.save(np.zeros(RATE * 2, dtype=np.float32), started_at=1_800_000_100.0)

    listed = store.list()
    assert [r.id for r in listed] == [newer.id, "1800000000000"]
    assert listed[0].duration == 2.0

    assert store.prune(older_than=1_800_000_050.0) == 1
    assert [r.id for r in store.list()] == [newer.id]


def test_store_rejects_path_tricks(tmp_path):
    store = RecordingStore(tmp_path)
    for bad in ("../state", "abc", "1800000000000.wav"):
        try:
            store.path_for(bad)
        except KeyError:
            continue
        raise AssertionError(bad)
    assert store.delete("../state") is False


def test_disabled_store_is_empty_and_saves_nothing():
    store = RecordingStore(None)
    assert store.list() == []
    assert store.save(np.zeros(RATE, dtype=np.float32), 1.0) is None


def test_transmissions_recorded_only_when_turned_on():
    live, service, recordings, clock = make_live()
    feed(live, transmission(1.5))
    settle(service, clock)
    assert recordings.list() == []

    service.update_config(record_transmissions=True)
    feed(live, transmission(1.5))
    [saved] = recordings.list()
    assert 1.5 <= saved.duration <= 2.5


def test_kerchunks_are_not_recorded():
    live, _service, recordings, _clock = make_live(record_transmissions=True)
    feed(live, transmission(0.3))
    assert recordings.list() == []


def test_parrot_records_next_transmission_without_repeating_and_plays_it_back():
    live, service, recordings, clock = make_live()
    service._run_action(RunAction("parrot"))
    assert service.controller.queued_announcements == ["tts:Parrot ready. Key up and speak."]
    service.controller._announcements.clear()

    feed(live, transmission(0.2)[: BLOCK * 5])
    assert live.engine.processor._repeating is False  # heard, but not repeated live
    feed(live, transmission(1.5)[BLOCK * 5 :])

    [saved] = recordings.list()
    assert service.controller.queued_announcements == [f"recording:{saved.id}"]
    assert len(live._renderer.render(f"recording:{saved.id}", service.config)) >= RATE

    service.controller._announcements.clear()
    settle(service, clock)
    second = transmission(1.5)  # parrot is one-shot
    feed(live, second[: BLOCK * 5])
    assert live.engine.processor._repeating is True
    feed(live, second[BLOCK * 5 :])
    assert len(recordings.list()) == 1


def test_parrot_gives_up_if_nobody_keys_up():
    live, service, recordings, clock = make_live()
    assert live.arm_parrot() is True
    clock["now"] += 120
    feed(live, transmission(1.5))
    assert recordings.list() == []


def test_parrot_needs_running_audio():
    live, service, _recordings, _clock = make_live()
    service.update_config(audio_enabled=False)
    assert live.arm_parrot() is False


def test_recording_endpoints(tmp_path):
    recordings = RecordingStore(tmp_path / "recordings")
    saved = recordings.save(np.zeros(RATE, dtype=np.float32), started_at=1_800_000_000.0)
    client = TestClient(create_app(start_background_tick=False, log_path=tmp_path / "t.log", recordings=recordings))

    [listed] = client.get("/api/recordings").json()
    assert listed["id"] == saved.id and listed["duration"] == 1.0

    audio = client.get(f"/api/recordings/{saved.id}/audio")
    assert audio.status_code == 200 and audio.content[:4] == b"RIFF"
    assert client.get("/api/recordings/..%2Fstate/audio").status_code == 404

    assert client.delete(f"/api/recordings/{saved.id}").status_code == 200
    assert client.get("/api/recordings").json() == []
    assert client.delete(f"/api/recordings/{saved.id}").status_code == 404
