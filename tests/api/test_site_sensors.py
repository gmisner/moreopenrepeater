import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from api.app import create_app
from api.notify import Notifier
from api.service import RepeaterService
from api.site_sensors import SiteSensors
from controller.macros import Macro
from controller.state_machine import RepeaterConfig

BATTERY = {
    "id": "battery",
    "name": "Battery",
    "type": "ads1115",
    "units": "V",
    "scale": 11.0,
    "offset": 0.0,
    "channel": 0,
    "high": 14.5,
    "low": 11.5,
    "hysteresis": 0.3,
    "alert": True,
    "speak_on_alert": False,
}
CABINET = {
    "id": "cabinet",
    "name": "Cabinet",
    "type": "ds18b20",
    "units": "C",
    "scale": 1.0,
    "offset": 0.0,
    "high": 40.0,
    "low": None,
    "hysteresis": 2.0,
    "alert": True,
    "speak_on_alert": True,
}


class FakeReader:
    def __init__(self, values: dict[str, float | None]) -> None:
        self.values = values
        self.calls: list[str] = []

    def __call__(self, sensor: dict) -> float | None:
        self.calls.append(sensor["id"])
        return self.values.get(sensor["id"])


def make(values=None, **config):
    reader = FakeReader(values or {"battery": 13.2, "cabinet": 22.0})
    service = RepeaterService(
        config=RepeaterConfig(
            callsign="W1AW",
            sensors_enabled=True,
            site_sensors=[BATTERY, CABINET],
            **config,
        ),
        macros=[
            Macro("*81", "battery", command="battery", action="sensor"),
            Macro("*80", "sensors", action="sensor"),
        ],
        clock=lambda: 0.0,
    )
    sensors = SiteSensors(service, reader=reader)
    return service, sensors, reader


def dial(service, digits):
    for digit in digits:
        service.simulate_dtmf(digit)


def test_poll_records_values_and_hysteresis_alerts():
    service, sensors, reader = make({"battery": 11.0, "cabinet": 22.0})
    alerts = sensors.poll()
    assert [a.key for a in alerts] == ["sensor:battery:low"]
    assert alerts[0].title == "Battery low"
    status = sensors.status()
    assert status["sensors"][0]["value"] == 11.0
    assert status["sensors"][0]["alert_state"] == "low"

    reader.values["battery"] = 11.6  # still inside hysteresis
    assert sensors.poll() == []
    assert sensors.status()["sensors"][0]["alert_state"] == "low"

    reader.values["battery"] = 12.0  # cleared
    cleared = sensors.poll()
    assert cleared[0].severity == "info"
    assert sensors.status()["sensors"][0]["alert_state"] is None


def test_high_threshold_and_speak_on_alert():
    _, sensors, reader = make({"battery": 13.0, "cabinet": 42.0})
    alerts = sensors.poll()
    assert [a.key for a in alerts] == ["sensor:cabinet:high"]
    # speak_on_alert queues TTS through the service
    assert any(clip.startswith("tts:Cabinet is") for clip in sensors._service.controller.queued_announcements)
    reader.values["cabinet"] = 37.0
    assert sensors.poll()[0].severity == "info"


def test_missing_hardware_skips_without_alert():
    _, sensors, _ = make({"battery": None, "cabinet": None})
    assert sensors.poll() == []
    rows = sensors.status()["sensors"]
    assert all(row["value"] is None and row["error"] == "unavailable" for row in rows)


def test_macro_speaks_one_or_all():
    service, sensors, _ = make()
    dial(service, "*81")
    assert service.controller.queued_announcements == ["tts:Battery thirteen point two volts"]
    dial(service, "*80")
    spoken = service.controller.queued_announcements[-1]
    assert "Battery thirteen point two volts" in spoken
    assert "Cabinet twenty two degrees Celsius" in spoken


def test_without_hook_the_macro_says_so():
    service = RepeaterService(
        config=RepeaterConfig(),
        macros=[Macro("*8", "sensors", action="sensor")],
        clock=lambda: 0.0,
    )
    dial(service, "*8")
    assert service.controller.queued_announcements == ["tts:Site sensors are not set up."]


def test_config_and_api_with_fake_reader():
    service, sensors, reader = make()
    tmp = Path(tempfile.mkdtemp())
    client = TestClient(
        create_app(
            service=service,
            site_sensors=sensors,
            start_background_tick=False,
            log_path=tmp / "t.log",
            notifier=Notifier(),
        )
    )

    body = client.get("/api/sensors").json()
    assert body["enabled"] is True
    assert len(body["sensors"]) == 2
    assert body["sensors"][0]["value"] is None  # no poll yet

    sensors.poll()
    body = client.get("/api/sensors").json()
    assert body["sensors"][0]["value"] == 13.2

    saved = client.put(
        "/api/config",
        json={
            "sensors_enabled": True,
            "sensors_poll_interval": 15,
            "site_sensors": [
                {
                    "id": "Battery",
                    "name": "Main battery",
                    "type": "ads1115",
                    "units": "V",
                    "scale": 11,
                    "channel": 1,
                    "low": 11.8,
                    "high": 14.4,
                }
            ],
        },
    ).json()
    assert saved["site_sensors"][0]["id"] == "battery"
    assert saved["sensors_poll_interval"] == 15

    assert client.put(
        "/api/config",
        json={"site_sensors": [{"id": "bad id", "name": "x", "type": "ds18b20"}]},
    ).status_code == 422

    macros = client.post("/api/macros", json={"pattern": "*9", "action": "sensor", "command": "battery"}).json()
    assert macros[-1]["command"] == "battery"
    assert client.post("/api/macros", json={"pattern": "*90", "action": "sensor", "command": ""}).status_code == 200
    assert reader.calls  # speech path may add more reads via separate tests
