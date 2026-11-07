from fastapi.testclient import TestClient

from api.app import create_app
from api.metrics import render_metrics
from api.service import RepeaterService
from controller.state_machine import RepeaterConfig


def make_client():
    service = RepeaterService(config=RepeaterConfig(hang_time=1.0, tot_duration=5.0), clock=lambda: 100.0)
    app = create_app(service=service, start_background_tick=False)
    return TestClient(app), service


def test_metrics_endpoint_is_prometheus_text():
    client, service = make_client()
    service.update_config(callsign="W1AW")

    response = client.get("/metrics")

    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    body = response.text
    for name in (
        "moreopenrepeater_up",
        "moreopenrepeater_ptt_active",
        "moreopenrepeater_cos_active",
        "moreopenrepeater_controller_state",
        "moreopenrepeater_audio_engine_running",
        "moreopenrepeater_rx_level_dbfs",
        "moreopenrepeater_activity_rx_count",
        "moreopenrepeater_process_uptime_seconds",
    ):
        assert name in body
    assert 'moreopenrepeater_controller_state{state="idle"} 1' in body


def test_render_metrics_tracks_ptt_and_cos():
    service = RepeaterService(config=RepeaterConfig(), clock=lambda: 50.0)
    service.ptt_active = True
    service.cos_active = True

    text = render_metrics(service=service, process_started_at=10.0, now=50.0)

    assert "moreopenrepeater_ptt_active 1" in text
    assert "moreopenrepeater_cos_active 1" in text
    assert "moreopenrepeater_process_uptime_seconds 40.0" in text


def test_version_endpoint_returns_installed_commit():
    client, _service = make_client()

    response = client.get("/api/version")

    assert response.status_code == 200
    body = response.json()
    assert body is None or (isinstance(body.get("sha"), str) and "subject" in body)
