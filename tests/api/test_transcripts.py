import io
import json
import sys
import types
import urllib.error

import numpy as np
import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.mailbox import MailboxStore
from api.recordings import RecordingStore
from api.service import RepeaterService
from api.transcripts import (
    MAX_ATTEMPTS,
    RETRY_SECONDS,
    Transcriber,
    TranscriptionError,
    VoskEngine,
    callsigns,
    read_transcript,
    transcribe_openai,
)
from api.users import UserStore
from controller.state_machine import RepeaterConfig

RATE = 16000
START = 1_800_000_000.0


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("This is W1AW, clear.", ["W1AW"]),
        ("whiskey one alpha whiskey", ["W1AW"]),
        ("This is a test, kilo one alpha bravo charlie mobile", ["K1ABC"]),
        ("W 1 A W monitoring", ["W1AW"]),
        ("VE3ABC and 2E0XYZ checking in", ["VE3ABC", "2E0XYZ"]),
        ("kd2abc for kd2abc", ["KD2ABC"]),
        ("WRAB123 on the GMRS repeater", ["WRAB123"]),
        ("Kilo Delta Niner X-ray Yankee Zulu", ["KD9XYZ"]),
        ("hi it's kilo one alpha bravo charlie, call me back", ["K1ABC"]),
        ("this is a kilo one alpha bravo charlie", ["K1ABC"]),
        ("I kilo one alpha bravo charlie", ["K1ABC"]),
        ("whiskey a one x", ["WA1X"]),
        ("the 146.94 repeater at 9 pm, see you at 7", []),
        ("", []),
    ],
)
def test_callsigns(text, expected):
    assert callsigns(text) == expected


class FakePost:
    def __init__(self, reply=b'{"text": " W1AW clear "}', error=None):
        self.calls = []
        self.reply = reply
        self.error = error

    def __call__(self, url, body, headers, timeout):
        self.calls.append((url, body, headers))
        if self.error:
            raise self.error
        return self.reply


def wav_file(tmp_path):
    store = RecordingStore(tmp_path, sample_rate=RATE)
    info = store.save(np.zeros(RATE, dtype=np.float32), START)
    return store.path_for(info.id)


def test_openai_compatible_request(tmp_path):
    post = FakePost()
    path = wav_file(tmp_path)
    text = transcribe_openai(path, "http://whisper.lan:8000/v1/audio/transcriptions", "whisper-1", "sk-test", "Repeater W1AW.", post)
    assert text == "W1AW clear"
    url, body, headers = post.calls[0]
    assert url == "http://whisper.lan:8000/v1/audio/transcriptions"
    assert headers["Authorization"] == "Bearer sk-test"
    assert headers["Content-Type"].startswith("multipart/form-data; boundary=")
    assert b'name="model"\r\n\r\nwhisper-1\r\n' in body
    assert b'name="prompt"\r\n\r\nRepeater W1AW.\r\n' in body
    assert b'name="file"; filename="1800000000000.wav"' in body
    assert path.read_bytes() in body


def test_openai_without_a_key_sends_no_authorization(tmp_path):
    post = FakePost()
    transcribe_openai(wav_file(tmp_path), "http://whisper.lan/v1/audio/transcriptions", "base.en", None, "", post)
    assert "Authorization" not in post.calls[0][2]
    assert b'name="prompt"' not in post.calls[0][1]


def test_openai_errors_are_readable(tmp_path):
    path = wav_file(tmp_path)
    error = urllib.error.HTTPError("http://x", 401, "Unauthorized", {}, io.BytesIO(b'{"error": "bad key"}'))
    with pytest.raises(TranscriptionError, match="401 Unauthorized"):
        transcribe_openai(path, "http://x", "whisper-1", "k", "", FakePost(error=error))
    with pytest.raises(TranscriptionError, match="couldn't reach"):
        transcribe_openai(path, "http://x", "whisper-1", "k", "", FakePost(error=urllib.error.URLError("refused")))
    with pytest.raises(TranscriptionError, match="no text"):
        transcribe_openai(path, "http://x", "whisper-1", "k", "", FakePost(reply=b"<html>"))
    with pytest.raises(TranscriptionError, match="no transcription server"):
        transcribe_openai(path, "", "whisper-1", "k", "", FakePost())


def test_vosk_needs_the_package_and_a_model(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "vosk", None)
    engine = VoskEngine(tmp_path / "vosk-model")
    assert engine.installed() is False
    with pytest.raises(TranscriptionError, match="isn't installed"):
        engine.transcribe(wav_file(tmp_path))


def test_vosk_feeds_16_bit_audio_and_joins_the_results(tmp_path, monkeypatch):
    fed = []

    class Recognizer:
        def __init__(self, model, rate):
            assert rate == 16000
            self.chunks = 0

        def AcceptWaveform(self, data):
            fed.append(len(data))
            self.chunks += 1
            return self.chunks == 2

        def Result(self):
            return json.dumps({"text": "this is whiskey one alpha whiskey"})

        def FinalResult(self):
            return json.dumps({"text": "clear"})

    fake = types.SimpleNamespace(Model=lambda path: object(), KaldiRecognizer=Recognizer, SetLogLevel=lambda level: None)
    monkeypatch.setitem(sys.modules, "vosk", fake)
    model_dir = tmp_path / "vosk-model"
    (model_dir / "am").mkdir(parents=True)
    engine = VoskEngine(model_dir)
    assert engine.installed() and engine.model_present()
    assert engine.transcribe(wav_file(tmp_path)) == "this is whiskey one alpha whiskey clear"
    assert sum(fed) == RATE * 2  # one second of 16-bit samples


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def make(tmp_path, post=None, **config):
    recordings = RecordingStore(tmp_path / "recordings", sample_rate=RATE)
    mailbox = MailboxStore(tmp_path / "mailbox", None, sample_rate=RATE)
    service = RepeaterService(config=RepeaterConfig(**{"callsign": "W1AW", "transcription_engine": "openai", **config}))
    clock = Clock()
    post = post or FakePost()
    transcriber = Transcriber(service, [recordings, mailbox], VoskEngine(tmp_path / "vosk"), "sk-test", post=post, clock=clock)
    return transcriber, service, recordings, mailbox, post, clock


def test_background_pass_transcribes_recordings_and_messages_once(tmp_path):
    transcriber, _service, recordings, mailbox, post, _clock = make(tmp_path)
    first = recordings.save(np.zeros(RATE, dtype=np.float32), START)
    recordings.save(np.zeros(RATE, dtype=np.float32), START + 60)
    message = mailbox.save("12", np.zeros(RATE, dtype=np.float32), START)

    assert transcriber.run_pending() == 3
    assert read_transcript(recordings, first.id) == "W1AW clear"
    assert read_transcript(mailbox, message.id) == "W1AW clear"
    assert transcriber.run_pending() == 0
    assert len(post.calls) == 3
    assert b"Amateur radio repeater W1AW." in post.calls[0][1]

    recordings.delete(first.id)
    assert not recordings.transcript_path(first.id).exists()
    mailbox.delete_message(message.id)
    assert not (tmp_path / "mailbox" / f"{message.id}.txt").exists()


def test_background_pass_does_nothing_when_off(tmp_path):
    transcriber, _service, recordings, _mailbox, post, _clock = make(tmp_path, transcription_engine="off")
    recordings.save(np.zeros(RATE, dtype=np.float32), START)
    assert transcriber.run_pending() == 0
    assert post.calls == []


def test_errors_back_off_and_eventually_give_up_on_a_clip(tmp_path):
    post = FakePost(error=urllib.error.URLError("refused"))
    transcriber, service, recordings, _mailbox, _post, clock = make(tmp_path, post=post)
    newest = recordings.save(np.zeros(RATE, dtype=np.float32), START + 60)
    recordings.save(np.zeros(RATE, dtype=np.float32), START)

    assert transcriber.run_pending() == 0
    assert "couldn't reach" in transcriber.status.last_error
    assert len(post.calls) == 1  # stops at the first error
    assert transcriber.run_pending() == 0
    assert len(post.calls) == 1  # backing off

    for _ in range(MAX_ATTEMPTS - 1):
        clock.now += RETRY_SECONDS + 1
        transcriber.run_pending()
    assert len(post.calls) == MAX_ATTEMPTS
    assert newest.id not in [clip_id for _source, clip_id in transcriber.pending()]

    post.error = None
    service.update_config(transcription_url="http://whisper.lan/v1/audio/transcriptions")  # new settings, fresh start
    assert transcriber.run_pending() == 2
    assert transcriber.status.last_error is None


def test_prune_takes_transcripts_too(tmp_path):
    recordings = RecordingStore(tmp_path / "recordings", sample_rate=RATE)
    info = recordings.save(np.zeros(RATE, dtype=np.float32), START)
    recordings.transcript_path(info.id).write_text("hi")
    recordings.prune(older_than=START + 1)
    assert list((tmp_path / "recordings").iterdir()) == []


def make_client(tmp_path, monkeypatch):
    monkeypatch.setenv("MOREOPENREPEATER_LOG_PATH", str(tmp_path / "t.log"))
    recordings = RecordingStore(tmp_path / "recordings", sample_rate=RATE)
    app = create_app(
        service=RepeaterService(config=RepeaterConfig(callsign="W1AW")), start_background_tick=False,
        users=UserStore(), recordings=recordings,
    )
    return TestClient(app), recordings


def test_recordings_carry_transcripts_and_can_be_searched(tmp_path, monkeypatch):
    client, recordings = make_client(tmp_path, monkeypatch)
    a = recordings.save(np.zeros(RATE, dtype=np.float32), START)
    b = recordings.save(np.zeros(RATE, dtype=np.float32), START + 60)
    c = recordings.save(np.zeros(RATE, dtype=np.float32), START + 120)
    recordings.transcript_path(a.id).write_text("this is whiskey one alpha whiskey for the net")
    recordings.transcript_path(b.id).write_text("K1ABC mobile, the weather is clearing")

    listed = client.get("/api/recordings").json()
    assert [r["id"] for r in listed] == [c.id, b.id, a.id]
    assert listed[0]["transcript"] is None and listed[0]["callsigns"] == []
    assert listed[2]["callsigns"] == ["W1AW"]

    assert [r["id"] for r in client.get("/api/recordings", params={"q": "w1aw"}).json()] == [a.id]
    assert [r["id"] for r in client.get("/api/recordings", params={"q": "Weather"}).json()] == [b.id]
    assert client.get("/api/recordings", params={"q": "nothing like it"}).json() == []


def test_transcription_status(tmp_path, monkeypatch):
    monkeypatch.delenv("MOREOPENREPEATER_TRANSCRIPTION_API_KEY", raising=False)
    client, recordings = make_client(tmp_path, monkeypatch)
    recordings.save(np.zeros(RATE, dtype=np.float32), START)
    status = client.get("/api/transcription").json()
    assert status["engine"] == "off" and status["pending"] == 0 and status["api_key_set"] is False

    assert client.put("/api/config", json={"transcription_engine": "vosk"}).status_code == 200
    status = client.get("/api/transcription").json()
    assert status["engine"] == "vosk" and status["pending"] == 1
    assert status["vosk_model_dir"].endswith("vosk-model")
    assert client.put("/api/config", json={"transcription_url": "ftp://nope"}).status_code == 422
