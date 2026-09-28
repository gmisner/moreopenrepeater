import json
import tempfile
from datetime import datetime
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from controller.state_machine import ANNOUNCING, RepeaterConfig

from api.app import create_app
from api.assets import AudioAssetStore
from api.service import RepeaterService
from playout.renderer import ClipRenderer

RATE = 8000
SAMPLE = json.loads((Path(__file__).parent.parent / "wx" / "nws_alerts_sample.json").read_text())
LOCATED = dict(callsign="W1AW", wx_lat=35.2, wx_lon=-101.8)


class FakeTTS:
    name = "fake"

    def __init__(self):
        self.spoken = []

    def synthesize(self, text, voice=""):
        self.spoken.append(text)
        return np.full(RATE, 0.1, dtype=np.float32), RATE


class FakeNWS:
    def __init__(self, response=SAMPLE, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def __call__(self, lat, lon, contact=""):
        self.calls.append((lat, lon, contact))
        if self.error:
            raise self.error
        return self.response


def make_client(nws=None, **config):
    tmp = Path(tempfile.mkdtemp())
    assets = AudioAssetStore(tmp / "audio")
    tts = FakeTTS()
    renderer = ClipRenderer(assets.path_for, tts=tts, sample_rate=RATE)
    service = RepeaterService(
        config=RepeaterConfig(**config), clock=lambda: 0.0, wall_clock=lambda: datetime(2026, 9, 28, 12, 0), renderer=renderer
    )
    app = create_app(
        service=service, start_background_tick=False, assets_store=assets, log_path=tmp / "t.log",
        renderer=renderer, fetch_weather=nws or FakeNWS(),
    )
    return TestClient(app), service, tts


def test_status_before_any_check():
    client, *_ = make_client()

    assert client.get("/api/weather").json() == {"enabled": False, "last_checked": None, "last_error": None, "alerts": []}


def test_check_requires_a_location():
    client, *_ = make_client()

    assert client.post("/api/weather/check").status_code == 400


def test_check_while_disabled_shows_alerts_without_announcing():
    nws = FakeNWS()
    client, service, tts = make_client(nws=nws, wx_min_severity="Moderate", **LOCATED)

    body = client.post("/api/weather/check").json()

    assert nws.calls == [(35.2, -101.8, "W1AW")]
    assert [a["event"] for a in body["alerts"]] == [
        "Dense Fog Advisory", "Special Weather Statement", "Flood Watch", "Flood Watch",
    ]
    assert not any(a["announced"] for a in body["alerts"])
    assert body["last_checked"] == "2026-09-28T12:00:00"
    assert service.controller.queued_announcements == []


def test_check_while_enabled_announces_alerts_meeting_the_threshold():
    client, service, tts = make_client(wx_alerts_enabled=True, wx_min_severity="Severe", **LOCATED)

    body = client.post("/api/weather/check").json()

    assert [a["event"] for a in body["alerts"]] == ["Flood Watch", "Flood Watch"]
    assert all(a["announced"] for a in body["alerts"])
    assert len(service.controller.queued_announcements) == 2
    assert tts.spoken[0].startswith("National Weather Service Flood Watch.")

    client.post("/api/weather/check")
    assert len(service.controller.queued_announcements) == 2  # not repeated


def test_failed_check_is_reported_not_raised():
    client, *_ = make_client(nws=FakeNWS(error=OSError("network unreachable")), **LOCATED)

    body = client.post("/api/weather/check").json()

    assert body["last_error"] == "network unreachable"
    assert body["last_checked"] is not None


def test_play_an_active_alert_now():
    client, service, _ = make_client(wx_min_severity="Moderate", **LOCATED)
    alert_id = client.post("/api/weather/check").json()["alerts"][0]["id"]

    assert client.post(f"/api/weather/alerts/{alert_id}/play").status_code == 200
    service.tick()
    assert service.controller.state == ANNOUNCING

    assert client.post("/api/weather/alerts/not-active/play").status_code == 404


def test_weather_settings_round_trip_through_config():
    client, *_ = make_client()

    body = client.put(
        "/api/config", json={"wx_alerts_enabled": True, "wx_lat": 35.2, "wx_lon": -101.8, "wx_min_severity": "Extreme"}
    ).json()
    assert (body["wx_alerts_enabled"], body["wx_lat"], body["wx_min_severity"]) == (True, 35.2, "Extreme")

    assert client.put("/api/config", json={"clear_wx_lat": True}).json()["wx_lat"] is None
    assert client.put("/api/config", json={"wx_poll_interval": 5}).status_code == 422


def test_weather_areas_fetch_zone_outlines_only_when_the_map_is_on():
    zone = "https://api.weather.gov/zones/county/TXC375"
    shape = {"type": "Polygon", "coordinates": [[[-102, 35], [-101, 35], [-101, 36], [-102, 35]]]}
    feed = {"features": [{"id": "a", "geometry": None, "properties": {
        "id": "a", "status": "Actual", "event": "Tornado Warning", "severity": "Extreme", "affectedZones": [zone],
    }}]}
    fetched = []

    def fetch_zone(url, contact=""):
        fetched.append(url)
        return shape

    for map_on in (False, True):
        tmp = Path(tempfile.mkdtemp())
        service = RepeaterService(config=RepeaterConfig(aprs_map_enabled=map_on, **LOCATED))
        app = create_app(service=service, start_background_tick=False, log_path=tmp / "t.log",
                         fetch_weather=FakeNWS(feed), fetch_zone=fetch_zone)
        client = TestClient(app)
        client.post("/api/weather/check")
        features = client.get("/api/weather/areas").json()["features"]
        assert len(features) == (1 if map_on else 0)
    assert fetched == [zone]
    assert features[0]["geometry"] == shape and features[0]["properties"]["event"] == "Tornado Warning"
