import tempfile
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from controller.state_machine import RepeaterConfig, TRANSMITTING_ID

from api.app import create_app
from api.assets import AudioAssetStore
from api.service import RepeaterService
from playout.renderer import ClipRenderer
from playout.wav import decode_wav

RATE = 8000


class FakeTTS:
    name = "fake"

    def synthesize(self, text, voice=""):
        return np.full(2 * RATE, 0.1, dtype=np.float32), RATE  # 2 seconds


def make_client(tts=FakeTTS(), config=RepeaterConfig(callsign="W1AW")):
    tmp = Path(tempfile.mkdtemp())
    assets = AudioAssetStore(tmp / "audio")
    renderer = ClipRenderer(assets.path_for, tts=tts, sample_rate=RATE)
    clock = {"now": 0.0}
    service = RepeaterService(config=config, clock=lambda: clock["now"], renderer=renderer)
    app = create_app(
        service=service, start_background_tick=False, assets_store=assets, log_path=tmp / "t.log", renderer=renderer
    )
    return TestClient(app), service, renderer, clock


def test_tts_info_reports_the_engine():
    client, *_ = make_client()
    assert client.get("/api/audio/tts").json() == {"engine": "fake"}


def test_tts_info_reports_none_without_an_engine():
    client, *_ = make_client(tts=None)
    assert client.get("/api/audio/tts").json() == {"engine": None}


def test_preview_returns_a_playable_wav_of_the_clip():
    client, *_ = make_client()

    response = client.post("/api/audio/preview", json={"clip": "courtesy_tone", "config": {"courtesy_tone_duration": 0.5}})

    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/wav"
    samples, rate = decode_wav(response.content)
    assert rate == RATE
    assert abs(len(samples) - RATE // 2) <= 2


def test_preview_applies_unsaved_overrides_without_saving_them():
    client, service, *_ = make_client()

    client.post("/api/audio/preview", json={"clip": "id", "config": {"id_mode": "cw", "callsign": "K1ABC"}})

    assert service.config.callsign == "W1AW"


def test_preview_honours_clear_flags():
    client, *_ = make_client()
    response = client.post(
        "/api/audio/preview", json={"clip": "courtesy_tone", "config": {"clear_courtesy_tone_asset_id": True}}
    )
    assert response.status_code == 200


def test_preview_rejects_invalid_override_values():
    client, *_ = make_client()
    response = client.post("/api/audio/preview", json={"clip": "id", "config": {"id_mode": "semaphore"}})
    assert response.status_code == 422


def test_preview_of_unknown_clip_is_404():
    client, *_ = make_client()
    assert client.post("/api/audio/preview", json={"clip": "nope"}).status_code == 404


def test_preview_of_tts_without_an_engine_is_503():
    client, *_ = make_client(tts=None)
    response = client.post("/api/audio/preview", json={"clip": "tts:hello"})
    assert response.status_code == 503


def test_id_state_lasts_as_long_as_the_rendered_voice_id():
    client, service, renderer, clock = make_client(
        config=RepeaterConfig(callsign="W1AW", id_mode="voice", id_interval=10.0, id_audio_duration=0.5, idle_id=True)
    )
    renderer.warm(service.config)  # 2s of fake speech

    clock["now"] = 10.0
    service.tick()
    assert service.controller.state == TRANSMITTING_ID
    clock["now"] = 11.0
    service.tick()
    assert service.controller.state == TRANSMITTING_ID  # the 0.5s guess would have ended it already
    clock["now"] = 12.0
    service.tick()
    assert service.controller.state == "idle"


def test_new_courtesy_styles_and_the_custom_tone_are_settings():
    client, service, *_ = make_client()
    for field in ("courtesy_tone_style", "courtesy_tone_link_style", "courtesy_tone_patch_style", "net_courtesy_tone_style"):
        assert client.put("/api/config", json={field: "bumblebee"}).json()[field] == "bumblebee"
    config = client.put("/api/config", json={"courtesy_tone_style": "custom", "courtesy_tone_custom": " 1000:120  0:30 1500:120 "}).json()
    assert config["courtesy_tone_custom"] == "1000:120 0:30 1500:120"
    for bad in ("", "1000", "1000:5", "0:100", "1:1 2:2 3:3 4:4 5:5"):
        assert client.put("/api/config", json={"courtesy_tone_custom": bad}).status_code == 422
    assert client.put("/api/config", json={"courtesy_tone_style": "siren"}).status_code == 422

    response = client.post("/api/audio/preview", json={"clip": "courtesy_tone", "config": {"courtesy_tone_custom": "1000:500"}})
    samples, _ = decode_wav(response.content)
    assert len(samples) == RATE // 2
    assert service.saved_config.courtesy_tone_custom == "1000:120 0:30 1500:120"
