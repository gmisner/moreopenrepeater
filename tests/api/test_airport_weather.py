import json
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from controller.macros import Macro
from controller.state_machine import RepeaterConfig
from wx.metar import MetarCache

from api.airport_weather import AirportWeather
from api.app import create_app
from api.service import RepeaterService

SAMPLE = {obs["icaoId"]: obs for obs in json.loads((Path(__file__).parents[1] / "wx" / "metar_sample.json").read_text())}
NOW = SAMPLE["KAMA"]["obsTime"] + 60


def fetch(icaos):
    return [SAMPLE[i] for i in icaos if i in SAMPLE]


def offline(icaos):
    raise OSError("offline")


def make(macros=(), fetcher=fetch, **config):
    service = RepeaterService(config=RepeaterConfig(callsign="W1AW", **config), macros=list(macros), clock=lambda: 0.0)
    weather = AirportWeather(service, MetarCache(fetcher, clock=lambda: NOW), wall_clock=lambda: NOW)
    return service, weather


def dial(service, digits):
    for digit in digits:
        service.simulate_dtmf(digit)


def test_macro_speaks_one_airport_by_its_listed_name():
    service, _ = make([Macro("*5", "wx", command="KAMA", action="metar")], metar_airports=[{"icao": "KAMA", "name": "Amarillo"}])
    dial(service, "*5")
    [spoken] = service.controller.queued_announcements
    assert spoken.startswith("tts:Amarillo weather, observed just now. Wind one eight zero at 5 knots.")


def test_blank_macro_reads_every_listed_airport_and_spells_unnamed_ones():
    service, weather = make(metar_airports=[{"icao": "KAMA", "name": "Amarillo"}, {"icao": "KGVW", "name": ""}, {"icao": "ZZZZ", "name": ""}])
    text = weather.text()
    assert text.startswith("Amarillo weather")
    assert "K G V W weather" in text
    assert text.endswith("Z Z Z Z weather is not available.")


def test_no_airports_and_offline():
    _, weather = make()
    assert weather.text() == "No airports are set up."
    _, weather = make(fetcher=offline)
    assert weather.text("KAMA") == "K A M A weather is not available."


def test_without_the_hook_the_macro_says_so():
    service = RepeaterService(config=RepeaterConfig(), macros=[Macro("*5", "wx", action="metar")], clock=lambda: 0.0)
    dial(service, "*5")
    assert service.controller.queued_announcements == ["tts:Airport weather is not set up."]


def test_config_macro_and_preview_through_the_api():
    service, weather = make()
    tmp = Path(tempfile.mkdtemp())
    client = TestClient(create_app(service=service, airport_weather=weather, start_background_tick=False, log_path=tmp / "t.log"))

    config = client.put("/api/config", json={"metar_airports": [{"icao": "kama", "name": "Amarillo"}]}).json()
    assert config["metar_airports"] == [{"icao": "KAMA", "name": "Amarillo"}]
    assert client.put("/api/config", json={"metar_airports": [{"icao": "AMA", "name": ""}]}).status_code == 422

    macros = client.post("/api/macros", json={"pattern": "*5", "action": "metar", "command": "kgvw"}).json()
    assert macros[0]["command"] == "KGVW"
    assert client.post("/api/macros", json={"pattern": "*6", "action": "metar", "command": ""}).status_code == 200
    assert client.post("/api/macros", json={"pattern": "*7", "action": "metar", "command": "AMARILLO"}).status_code == 422

    assert client.get("/api/metar/kama").json()["text"].startswith("Amarillo weather")
    assert client.get("/api/metar/AMARILLO").status_code == 422
