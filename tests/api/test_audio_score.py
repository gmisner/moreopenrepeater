from fastapi.testclient import TestClient

from api.app import create_app
from api.service import RepeaterService
from controller.state_machine import RepeaterConfig
from playout.wav import encode_wav
import numpy as np


def make_client():
    service = RepeaterService(config=RepeaterConfig(), clock=lambda: 0.0)
    app = create_app(service=service, start_background_tick=False)
    return TestClient(app), service


def test_score_uploaded_wav():
    client, _ = make_client()
    rate = 8000
    samples = (0.2 * np.sin(2 * np.pi * 440 * np.arange(rate) / rate)).astype(np.float32)
    wav = encode_wav(samples, rate)

    response = client.post(
        "/api/audio/score",
        files={"file": ("rx.wav", wav, "audio/wav")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["sample_rate"] == rate
    assert body["duration_seconds"] == 1.0
    assert body["clipping"] is False
    assert body["peak_dbfs"] < 0


def test_score_rejects_non_wav():
    client, _ = make_client()
    response = client.post(
        "/api/audio/score",
        files={"file": ("rx.bin", b"not a wav", "application/octet-stream")},
    )
    assert response.status_code == 400


def test_record_score_needs_live_audio():
    client, _ = make_client()
    response = client.post("/api/audio/record-score?seconds=1")
    assert response.status_code == 409
    assert "Live audio" in response.json()["detail"]
