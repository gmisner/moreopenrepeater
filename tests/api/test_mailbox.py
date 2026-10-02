import base64
import tempfile
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.assets import AudioAssetStore
from api.live_audio import LiveAudio
from api.mailbox import MAX_MESSAGES, MAX_PIN_FAILURES, PIN_LOCKOUT_SECONDS, Mailbox, MailboxStore, is_mailbox_clip
from api.persistence import StateStore
from api.service import RepeaterService
from api.users import UserStore
from audio_io.engine import AudioEngine
from controller.events import MailboxCommand
from controller.state_machine import RepeaterConfig
from playout.renderer import ClipRenderer

RATE = 16000
BLOCK = RATE // 50
START = 1_800_000_000.0


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


class FakeTTS:
    name = "fake"

    def synthesize(self, text, voice=""):
        return np.full(RATE // 10, 0.1, dtype=np.float32), RATE


class ImmediateLoop:
    def call_soon_threadsafe(self, fn, *args):
        fn(*args)

    def run_in_executor(self, _executor, fn, *args):
        fn(*args)


def make(audio=True, **config):
    tmp = Path(tempfile.mkdtemp())
    store = MailboxStore(tmp / "mailbox", StateStore(tmp / "mailbox" / "boxes.json"), sample_rate=RATE)
    renderer = ClipRenderer(
        AudioAssetStore(tmp / "audio").path_for, tts=FakeTTS(), sample_rate=RATE,
        recording_path=lambda clip_id: store.path_for(clip_id) if is_mailbox_clip(clip_id) else None,
    )
    clock = {"now": START}
    service = RepeaterService(
        config=RepeaterConfig(audio_enabled=audio, vox_threshold_db=-30, vox_hold=0.1, mailbox_enabled=True, **config),
        clock=lambda: clock["now"],
        renderer=renderer,
    )
    live = LiveAudio(
        service, renderer, engine_factory=lambda *a, **kw: AudioEngine(*a, stream_factory=FakeStream, **kw),
        clock=lambda: clock["now"],
    )
    live.attach(ImmediateLoop())
    service.audio_output = live
    mailbox = Mailbox(service, store, renderer, clock=lambda: clock["now"])
    store.set_box("12", "Alice", "1234")
    return mailbox, service, live, store, clock


def transmission(seconds, level=0.3):
    t = np.arange(int(RATE * seconds)) / RATE
    voice = (level * np.sin(2 * np.pi * 1000 * t)).astype(np.float32)
    return np.concatenate([voice, np.zeros(RATE // 2, dtype=np.float32)])


def feed(live, signal):
    for start in range(0, len(signal), BLOCK):
        live.engine.process_one(signal[start : start + BLOCK])


def spoken(service):
    said = list(service.controller.queued_announcements)
    service.controller._announcements.clear()
    return said


def dial(service, digits):
    for digit in digits:
        service.simulate_dtmf(digit)


def leave_message(service, live, clock, seconds=1.5):
    dial(service, "*712#")
    spoken(service)
    feed(live, transmission(seconds))
    clock["now"] += 5


# -- store ------------------------------------------------------------------


def test_store_keeps_mailboxes_and_messages(tmp_path):
    boxes = StateStore(tmp_path / "boxes.json")
    store = MailboxStore(tmp_path, boxes, sample_rate=RATE)
    store.set_box("3", "Bob", "9999")
    store.set_box("3", "Bob K", None)  # keeps the PIN
    assert MailboxStore(tmp_path, boxes).boxes() == {"3": {"name": "Bob K", "pin": "9999"}}

    first = store.save("3", np.zeros(RATE, dtype=np.float32), START)
    store.save("3", np.zeros(RATE * 2, dtype=np.float32), START + 60)
    assert first.duration == 1.0
    assert [m.left_at for m in store.messages("3")] == [START, START + 60]

    store.sidecars(first.id)[0].write_text("hello")
    assert store.prune(older_than=START + 30, now=START + 30) == 1
    assert not store.sidecars(first.id)[0].exists()
    assert len(store.messages()) == 1

    assert store.delete_box("3") is True
    assert store.messages() == []
    assert store.delete_box("3") is False


def test_store_rejects_path_tricks(tmp_path):
    store = MailboxStore(tmp_path, None)
    for bad in ("../boxes", "mailbox-1", "mailbox-1-123", "1800000000000", "mailbox-12-1800000000000.wav"):
        with pytest.raises(KeyError):
            store.path_for(bad)
    assert store.delete_message("../boxes") is False


def test_store_prunes_old_playback(tmp_path):
    store = MailboxStore(tmp_path, None, sample_rate=RATE)
    playback = store.save_playback(np.zeros(RATE, dtype=np.float32), START)
    store.prune(older_than=0, now=START + 60)
    assert store.path_for(playback).exists()
    store.prune(older_than=0, now=START + 3600)
    assert not store.path_for(playback).exists()


# -- over the air -----------------------------------------------------------


def test_leaving_a_message_records_the_next_transmission_instead_of_repeating_it():
    _mailbox, service, live, store, clock = make()
    dial(service, "*712#")
    assert spoken(service) == ["tts:Mailbox 1 2. Key up and leave your message."]

    signal = transmission(1.5)
    feed(live, signal[: BLOCK * 5])
    assert live.engine.processor._repeating is False
    feed(live, signal[BLOCK * 5 :])

    [message] = store.messages("12")
    assert 1.5 <= message.duration <= 2.5
    assert message.left_at == START
    assert spoken(service) == ["tts:Message saved for mailbox 1 2."]


def test_leaving_needs_a_real_mailbox_and_live_audio():
    _mailbox, service, _live, _store, _clock = make()
    dial(service, "*77#")
    assert spoken(service) == ["tts:There is no mailbox 7."]

    _mailbox, service, _live, _store, _clock = make(audio=False)
    dial(service, "*712#")
    assert spoken(service) == ["tts:Messages need live audio, which is not running."]


def test_a_full_mailbox_takes_no_more():
    mailbox, service, _live, store, _clock = make()
    for i in range(MAX_MESSAGES):
        store.save("12", np.zeros(RATE, dtype=np.float32), START + i)
    mailbox.handle(MailboxCommand("leave", "12"))
    assert spoken(service) == ["tts:Mailbox 1 2 is full."]


def test_playing_messages_sends_one_clip_with_each_message_introduced():
    _mailbox, service, live, store, clock = make()
    leave_message(service, live, clock)
    leave_message(service, live, clock)
    spoken(service)

    dial(service, "*812*1234#")
    [clip] = spoken(service)
    assert clip.startswith("recording:mailbox-play-")
    samples = live._renderer.render(clip, service.config)
    voice = sum(m.duration for m in store.messages("12"))
    assert len(samples) / RATE > voice + 0.4  # intro, two time stamps, gaps and sign-off


def test_playing_an_empty_mailbox():
    mailbox, service, _live, _store, _clock = make()
    mailbox.handle(MailboxCommand("play", "12", "1234"))
    assert spoken(service) == ["tts:No messages for mailbox 1 2."]


def test_deleting_messages():
    mailbox, service, _live, store, _clock = make()
    store.save("12", np.zeros(RATE, dtype=np.float32), START)
    store.save("12", np.zeros(RATE, dtype=np.float32), START + 1)
    mailbox.handle(MailboxCommand("delete", "12", "1234"))
    assert spoken(service) == ["tts:2 messages deleted."]
    assert store.messages() == []


def test_wrong_pins_lock_the_mailbox():
    mailbox, service, _live, store, clock = make()
    store.save("12", np.zeros(RATE, dtype=np.float32), START)
    audits = []
    mailbox.audit_hook = lambda *entry: audits.append(entry)
    for _ in range(MAX_PIN_FAILURES):
        mailbox.handle(MailboxCommand("delete", "12", "0000"))
    assert spoken(service) == ["tts:Wrong PIN."] * MAX_PIN_FAILURES
    assert audits[-1] == ("DTMF", "Wrong mailbox PIN", "mailbox 12")

    mailbox.handle(MailboxCommand("delete", "12", "1234"))
    assert spoken(service) == ["tts:That mailbox is locked. Try again later."]
    assert len(store.messages()) == 1

    clock["now"] += PIN_LOCKOUT_SECONDS + 1
    mailbox.handle(MailboxCommand("delete", "12", "1234"))
    assert spoken(service) == ["tts:1 message deleted."]


def test_a_mailbox_without_a_pin_cannot_be_opened():
    mailbox, service, _live, store, _clock = make()
    store.set_box("5", "Club", "")
    mailbox.handle(MailboxCommand("play", "5", ""))
    assert spoken(service) == ["tts:Wrong PIN."]


def test_mailbox_off_means_no_hook_is_reached():
    _mailbox, service, _live, _store, _clock = make()
    service.update_config(mailbox_enabled=False)
    dial(service, "*712#")
    assert spoken(service) == []


def test_without_a_mailbox_the_service_says_so():
    service = RepeaterService(config=RepeaterConfig(mailbox_enabled=True))
    dial(service, "*712#")
    assert service.controller.queued_announcements == ["tts:The mailbox is not available."]


# -- housekeeping -----------------------------------------------------------


def test_reminders_name_waiting_mailboxes_on_schedule():
    mailbox, service, _live, store, clock = make(mailbox_reminder_minutes=30)
    store.set_box("3", "Bob", "1111")
    store.set_box("40", "Carol", "2222")
    assert mailbox.reminder_text() is None

    store.save("12", np.zeros(RATE, dtype=np.float32), START)
    assert mailbox.reminder_text() == "Messages waiting for mailbox 1 2."
    store.save("3", np.zeros(RATE, dtype=np.float32), START)
    store.save("40", np.zeros(RATE, dtype=np.float32), START)
    assert mailbox.reminder_text() == "Messages waiting for mailboxes 3, 1 2 and 4 0."

    clock["now"] += 10 * 60
    mailbox.remind()
    assert spoken(service) == []
    clock["now"] += 25 * 60
    mailbox.remind()
    assert spoken(service) == ["tts:Messages waiting for mailboxes 3, 1 2 and 4 0."]
    mailbox.remind()
    assert spoken(service) == []


def test_no_reminders_during_a_net_or_when_turned_off():
    mailbox, service, _live, store, clock = make(mailbox_reminder_minutes=30)
    store.save("12", np.zeros(RATE, dtype=np.float32), START)
    mailbox._net_active = lambda: True
    clock["now"] += 31 * 60
    mailbox.remind()
    assert spoken(service) == []

    service.update_config(mailbox_reminder_minutes=0)
    clock["now"] += 31 * 60
    mailbox.remind()
    assert spoken(service) == []


def test_old_messages_expire():
    mailbox, _service, _live, store, clock = make(mailbox_retention_days=2)
    store.save("12", np.zeros(RATE, dtype=np.float32), START)
    clock["now"] += 86400
    assert mailbox.prune() == 0
    clock["now"] += 2 * 86400
    assert mailbox.prune() == 1
    assert store.messages() == []


# -- dashboard --------------------------------------------------------------


def make_client(tmp_path, monkeypatch, users=None):
    monkeypatch.setenv("MOREOPENREPEATER_LOG_PATH", str(tmp_path / "t.log"))
    store = MailboxStore(tmp_path / "mailbox", StateStore(tmp_path / "mailbox" / "boxes.json"), sample_rate=RATE)
    app = create_app(
        service=RepeaterService(config=RepeaterConfig(callsign="W1AW")), start_background_tick=False,
        users=users if users is not None else UserStore(), mailbox_store=store,
    )
    return TestClient(app), store


def basic(username, password):
    return {"Authorization": "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()}


def test_mailbox_endpoints(tmp_path, monkeypatch):
    client, store = make_client(tmp_path, monkeypatch)
    assert client.put("/api/mailbox/boxes/12", json={"name": "Alice"}).status_code == 400  # a new one needs a PIN
    assert client.put("/api/mailbox/boxes/12", json={"name": "Alice", "pin": "12"}).status_code == 422
    assert client.put("/api/mailbox/boxes/abc", json={"name": "x", "pin": "1234"}).status_code == 422

    body = client.put("/api/mailbox/boxes/12", json={"name": "Alice", "pin": "1234"}).json()
    assert body == {"boxes": [{"box": "12", "name": "Alice", "pin_set": True, "messages": 0}], "messages": []}
    assert "1234" not in client.get("/api/mailbox").text

    client.put("/api/mailbox/boxes/12", json={"name": "Alice K"})
    assert store.boxes()["12"] == {"name": "Alice K", "pin": "1234"}

    message = store.save("12", np.zeros(RATE, dtype=np.float32), START)
    listed = client.get("/api/mailbox").json()
    assert listed["boxes"][0]["messages"] == 1
    assert listed["messages"][0]["id"] == message.id

    audio = client.get(f"/api/mailbox/messages/{message.id}/audio")
    assert audio.status_code == 200 and audio.headers["content-type"] == "audio/wav"
    playback = store.save_playback(np.zeros(RATE, dtype=np.float32), START)
    assert client.get(f"/api/mailbox/messages/{playback}/audio").status_code == 404
    assert client.get("/api/mailbox/messages/..%2Fboxes/audio").status_code == 404

    assert client.delete(f"/api/mailbox/messages/{message.id}").json()["messages"] == []
    assert client.delete(f"/api/mailbox/messages/{message.id}").status_code == 404
    assert client.delete("/api/mailbox/boxes/12").json()["boxes"] == []
    assert client.delete("/api/mailbox/boxes/12").status_code == 404


def test_mailbox_endpoints_are_admin_only(tmp_path, monkeypatch):
    users = UserStore()
    users.add("alice", "password1", "admin")
    users.add("olly", "password1", "operator")
    client, _store = make_client(tmp_path, monkeypatch, users)
    assert client.get("/api/mailbox").status_code == 401
    assert client.get("/api/mailbox", headers=basic("olly", "password1")).status_code == 403
    assert client.get("/api/mailbox", headers=basic("alice", "password1")).status_code == 200


def test_mailbox_settings_round_trip(tmp_path, monkeypatch):
    client, _store = make_client(tmp_path, monkeypatch)
    saved = client.put("/api/config", json={"mailbox_enabled": True, "mailbox_leave_code": "*71", "mailbox_reminder_minutes": 0}).json()
    assert saved["mailbox_enabled"] is True and saved["mailbox_leave_code"] == "*71"
    assert client.put("/api/config", json={"mailbox_leave_code": "*7#"}).status_code == 422
