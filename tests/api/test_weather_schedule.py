"""Scheduled weather announcements: interval, content, Linked Radio flag."""
from __future__ import annotations

from datetime import datetime, timedelta

from controller.state_machine import RepeaterConfig
from wx.nws import WeatherAlert

from api.service import RepeaterService


START = datetime(2026, 11, 7, 12, 0)


def make_service(**config) -> tuple[RepeaterService, dict]:
    clock = {"wall": START}

    def wall_clock():
        return clock["wall"]

    service = RepeaterService(config=RepeaterConfig(**config), clock=lambda: 0.0, wall_clock=wall_clock)
    return service, clock


def test_schedule_is_off_by_default():
    service, _ = make_service()
    assert not service.weather_schedule_due()
    assert service.next_weather_schedule_run() is None


def test_enabling_schedule_arms_the_next_run():
    service, clock = make_service(wx_schedule_enabled=True, wx_schedule_minutes=30)

    assert not service.weather_schedule_due()
    assert service.next_weather_schedule_run() == START + timedelta(minutes=30)

    clock["wall"] = START + timedelta(minutes=29)
    assert not service.weather_schedule_due()

    clock["wall"] = START + timedelta(minutes=30)
    assert service.weather_schedule_due()
    assert service.next_weather_schedule_run() == START + timedelta(minutes=60)


def test_changing_interval_resets_the_timer():
    service, clock = make_service(wx_schedule_enabled=True, wx_schedule_minutes=60)
    first = service.next_weather_schedule_run()
    clock["wall"] = START + timedelta(minutes=10)
    service.update_config(wx_schedule_minutes=15)
    assert service.next_weather_schedule_run() == clock["wall"] + timedelta(minutes=15)
    assert service.next_weather_schedule_run() != first


def test_metar_only_skips_when_no_airports():
    service, _ = make_service(wx_schedule_enabled=True, wx_schedule_content="metar")
    service.weather.metar_text = lambda: "No airports are set up."
    assert service.scheduled_weather_text() is None


def test_metar_content_uses_airport_text():
    service, _ = make_service(wx_schedule_enabled=True, wx_schedule_content="metar")
    service.weather.metar_text = lambda: "Amarillo weather. Temperature 70."
    assert service.scheduled_weather_text() == "Amarillo weather. Temperature 70."


def test_alerts_only_skips_when_quiet():
    service, _ = make_service(
        wx_schedule_enabled=True, wx_schedule_content="alerts", wx_lat=35.2, wx_lon=-101.8
    )
    assert service.scheduled_weather_text() is None


def test_alerts_content_speaks_active_alerts():
    service, _ = make_service(
        wx_schedule_enabled=True, wx_schedule_content="alerts", wx_lat=35.2, wx_lon=-101.8
    )
    service.weather_alerts = [
        WeatherAlert(
            id="a1",
            event="Flood Watch",
            severity="Severe",
            urgency="Expected",
            headline="Flood Watch for Potter",
            nws_headline="Flood Watch",
            area="Potter",
            message_type="Alert",
            expires=None,
            ends=None,
        )
    ]
    text = service.scheduled_weather_text()
    assert text is not None
    assert "one active weather alert" in text
    assert "Flood Watch" in text


def test_both_combines_metar_and_alerts():
    service, _ = make_service(
        wx_schedule_enabled=True, wx_schedule_content="both", wx_lat=35.2, wx_lon=-101.8
    )
    service.weather.metar_text = lambda: "Amarillo weather."
    service.weather_alerts = [
        WeatherAlert(
            id="a1",
            event="Flood Watch",
            severity="Severe",
            urgency="Expected",
            headline="Flood Watch for Potter",
            nws_headline="Flood Watch",
            area="Potter",
            message_type="Alert",
            expires=None,
            ends=None,
        )
    ]
    text = service.scheduled_weather_text()
    assert text is not None
    assert text.startswith("Amarillo weather.")
    assert "Flood Watch" in text


def test_schedule_settings_round_trip_through_config():
    from pathlib import Path
    import tempfile

    import numpy as np
    from fastapi.testclient import TestClient

    from api.app import create_app
    from api.assets import AudioAssetStore
    from playout.renderer import ClipRenderer

    class FakeTTS:
        name = "fake"

        def synthesize(self, text, voice="", speed=1.0):
            return np.full(8000, 0.1, dtype=np.float32), 8000

    tmp = Path(tempfile.mkdtemp())
    assets = AudioAssetStore(tmp / "audio")
    renderer = ClipRenderer(assets.path_for, tts=FakeTTS(), sample_rate=8000)
    service = RepeaterService(config=RepeaterConfig(), clock=lambda: 0.0, wall_clock=lambda: START, renderer=renderer)
    client = TestClient(
        create_app(service=service, start_background_tick=False, assets_store=assets, log_path=tmp / "t.log", renderer=renderer)
    )

    body = client.put(
        "/api/config",
        json={
            "wx_schedule_enabled": True,
            "wx_schedule_minutes": 45,
            "wx_schedule_content": "both",
            "wx_schedule_on_linked_radio": True,
        },
    ).json()
    assert body["wx_schedule_enabled"] is True
    assert body["wx_schedule_minutes"] == 45
    assert body["wx_schedule_content"] == "both"
    assert body["wx_schedule_on_linked_radio"] is True
    assert client.put("/api/config", json={"wx_schedule_minutes": 2}).status_code == 422


def test_deferred_schedule_clip_carries_linked_radio_flag():
    service, _ = make_service(net_hold_announcements=True)
    service.set_net_active(True)
    service.defer_announcement("tts:Amarillo weather.", on_linked_radio=True)
    assert service.due_announcements() == []
    service.set_net_active(False)
    assert service.due_announcements() == [("tts:Amarillo weather.", True)]
