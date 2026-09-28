import tempfile
import time
from datetime import datetime
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from controller.announcements import Announcement
from controller.macros import Macro
from controller.state_machine import IDLE, RECEIVING, RepeaterConfig

from api.app import create_app
from api.assets import AudioAssetStore
from api.service import RepeaterService, talking_clock_text
from playout.renderer import ClipRenderer

RATE = 8000
NOW = datetime(2026, 9, 28, 21, 5)


class FakeTTS:
    name = "fake"

    def synthesize(self, text, voice=""):
        return np.full(RATE, 0.1, dtype=np.float32), RATE


def make_service(macros, **config):
    clock = {"now": 0.0}
    service = RepeaterService(
        config=RepeaterConfig(callsign="W1AW", hang_time=1.0, **config),
        macros=macros,
        clock=lambda: clock["now"],
        wall_clock=lambda: NOW,
    )
    return service, clock


def dial(service, digits):
    for digit in digits:
        service.simulate_dtmf(digit)


def test_talking_clock_text():
    assert talking_clock_text(datetime(2026, 1, 1, 21, 5)) == "The time is 9 oh 5 P M."
    assert talking_clock_text(datetime(2026, 1, 1, 0, 0)) == "The time is 12 o'clock A M."
    assert talking_clock_text(datetime(2026, 1, 1, 11, 42)) == "The time is 11 42 A M."


def test_time_macro_speaks_the_time():
    service, _ = make_service([Macro("*1", "time", action="time")])
    dial(service, "*1")
    assert service.controller.queued_announcements == ["tts:The time is 9 oh 5 P M."]


def test_weather_macro_without_a_location():
    service, _ = make_service([Macro("*2", "wx", action="weather")])
    dial(service, "*2")
    assert service.controller.queued_announcements == ["tts:Weather alerts are not set up."]


def test_weather_macro_with_no_alerts():
    service, _ = make_service([Macro("*2", "wx", action="weather")], wx_lat=35.2, wx_lon=-101.8)
    dial(service, "*2")
    assert service.controller.queued_announcements == ["tts:There are no active weather alerts."]


def test_say_id_and_announcement_macros():
    service, _ = make_service(
        [
            Macro("*3", "say", command="Club meeting Thursday", action="say"),
            Macro("*4", "id", action="id"),
            Macro("*5", "net", command="net", action="announcement"),
        ]
    )
    service.scheduler.upsert(Announcement(id="net", name="Net", message="Net tonight"), NOW)

    dial(service, "*3*4*5")

    assert service.controller.queued_announcements == ["tts:Club meeting Thursday", "id", "tts:Net tonight"]


def test_transmitter_disable_stops_repeating_and_enable_restores_it():
    service, clock = make_service(
        [Macro("*99123", "off", action="tx_disable"), Macro("*99456", "on", action="tx_enable")]
    )
    service.simulate_cos(True)
    assert service.controller.state == RECEIVING

    dial(service, "*99123")  # keyed while the user is still transmitting

    assert service.controller.state == IDLE and not service.ptt_active
    assert service.config.transmitter_enabled is False
    service.simulate_cos(False)
    service.simulate_cos(True)
    assert service.controller.state == IDLE  # no longer repeats
    service.simulate_cos(False)

    dial(service, "*99456")
    assert service.config.transmitter_enabled is True
    assert service.controller.queued_announcements == ["tts:Transmitter enabled"]
    service.simulate_cos(True)
    assert service.controller.state == RECEIVING


def test_disabled_transmitter_holds_ids_and_announcements():
    service, clock = make_service([], transmitter_enabled=False, id_interval=10.0)
    service.queue_announcement("tts:hello")
    clock["now"] = 20.0
    service.tick()
    assert service.controller.state == IDLE and not service.ptt_active


def test_macro_api_validates_action_arguments():
    tmp = Path(tempfile.mkdtemp())
    client = TestClient(create_app(start_background_tick=False, log_path=tmp / "t.log"))

    assert client.post("/api/macros", json={"pattern": "*1", "description": "t", "action": "time"}).status_code == 200
    assert client.post("/api/macros", json={"pattern": "*3", "description": "s", "action": "say"}).status_code == 422
    assert client.post("/api/macros", json={"pattern": "*x", "description": "t", "action": "time"}).status_code == 422


def test_spoken_actions_are_rendered_before_queueing():
    tmp = Path(tempfile.mkdtemp())
    assets = AudioAssetStore(tmp / "audio")
    renderer = ClipRenderer(assets.path_for, tts=FakeTTS(), sample_rate=RATE)
    service = RepeaterService(
        config=RepeaterConfig(), macros=[Macro("*1", "time", action="time")], renderer=renderer, wall_clock=lambda: NOW
    )
    app = create_app(service=service, start_background_tick=False, assets_store=assets, log_path=tmp / "t.log", renderer=renderer)

    with TestClient(app) as client:
        for digit in "*1":
            client.post("/api/simulate/dtmf", json={"digit": digit})
        deadline = time.monotonic() + 2
        while not service.controller.queued_announcements and time.monotonic() < deadline:
            time.sleep(0.01)

    clip = "tts:The time is 9 oh 5 P M."
    assert service.controller.queued_announcements == [clip]
    assert renderer.cached_duration(clip, service.config) == 1.0
