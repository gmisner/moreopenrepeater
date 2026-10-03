import json
from pathlib import Path

from wx.metar import CACHE_SECONDS, STALE_SECONDS, MetarCache, speech_text

SAMPLE = {obs["icaoId"]: obs for obs in json.loads((Path(__file__).parent / "metar_sample.json").read_text())}
OBSERVED = SAMPLE["KAMA"]["obsTime"]


def test_low_visibility_fog_in_miles():
    text = speech_text(SAMPLE["KAMA"], "Amarillo", "mi", OBSERVED + 6 * 60)
    assert text == (
        "Amarillo weather, observed 6 minutes ago. Wind one eight zero at 5 knots. Visibility one quarter mile. Fog. "
        "Sky obscured, vertical visibility 200 feet. Temperature 57, dew point 56 degrees Fahrenheit. Altimeter 30.25."
    )


def test_metric_units():
    text = speech_text(SAMPLE["KAMA"], "Amarillo", "km", OBSERVED)
    assert "Visibility 0.4 kilometers." in text
    assert "Temperature 14, dew point 13 degrees Celsius." in text
    assert "Altimeter 1024 hectopascals." in text
    assert "observed just now" in text


def test_gusts_and_cloud_layers():
    text = speech_text(SAMPLE["KGVW"], "Galveston", "mi", OBSERVED)
    assert "Wind zero five zero at 15 knots, gusting 21." in text
    assert "Visibility 9 miles." in text
    assert "Clouds few at 1500 feet, few at 2900 feet, scattered at 6000 feet." in text


def test_variable_wind_and_unlimited_visibility():
    text = speech_text(SAMPLE["KAHN"], "Athens", "mi", OBSERVED)
    assert "Wind variable at 3 knots." in text
    assert "Visibility 10 miles or more." in text
    assert "Clouds overcast at 500 feet." in text


def test_rain_and_mist_with_a_fractional_visibility():
    text = speech_text(SAMPLE["KACT"], "Waco", "mi", OBSERVED)
    assert "Visibility 2 and a half miles." in text
    assert "Rain, mist." in text


def test_weather_codes():
    obs = {"wspd": 0, "wxString": "+TSRA VCSH -FZDZ BLSN", "clouds": [{"cover": "CLR"}]}
    assert speech_text(obs, "Test", "mi", 0) == (
        "Test weather. Wind calm. Heavy thunderstorm with rain, showers nearby, light freezing drizzle, blowing snow. Sky clear."
    )


def test_negative_temperatures():
    assert "Temperature minus 4, dew point minus 9 degrees Celsius." in speech_text({"temp": -4.2, "dewp": -9}, "X", "km", 0)


class FakeFetch:
    def __init__(self, reports):
        self.reports = reports
        self.calls = []
        self.fail = False

    def __call__(self, icaos):
        self.calls.append(list(icaos))
        if self.fail:
            raise OSError("offline")
        return [self.reports[i] for i in icaos if i in self.reports]


def test_cache_keeps_reports_for_ten_minutes():
    clock = {"now": OBSERVED}
    fetch = FakeFetch(SAMPLE)
    cache = MetarCache(fetch, clock=lambda: clock["now"])
    assert cache.get(["KAMA", "KGVW"])["KAMA"]["icaoId"] == "KAMA"
    cache.get(["KAMA"])
    assert fetch.calls == [["KAMA", "KGVW"]]
    clock["now"] += CACHE_SECONDS
    cache.get(["KAMA"])
    assert fetch.calls[-1] == ["KAMA"]


def test_unknown_airport_is_none():
    cache = MetarCache(FakeFetch(SAMPLE), clock=lambda: OBSERVED)
    assert cache.get(["ZZZZ"]) == {"ZZZZ": None}


def test_offline_falls_back_to_a_recent_report_then_gives_up():
    clock = {"now": OBSERVED}
    fetch = FakeFetch(SAMPLE)
    cache = MetarCache(fetch, clock=lambda: clock["now"])
    cache.get(["KAMA"])
    fetch.fail = True
    clock["now"] += CACHE_SECONDS + 1
    assert cache.get(["KAMA"])["KAMA"] is not None
    clock["now"] = OBSERVED + STALE_SECONDS
    assert cache.get(["KAMA"])["KAMA"] is None
    assert cache.get(["KGVW"])["KGVW"] is None
