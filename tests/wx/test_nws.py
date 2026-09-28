import json
from datetime import datetime, timedelta
from pathlib import Path

from wx.nws import AlertTracker, WeatherAlert, meets_severity, parse_alerts, speech_text

SAMPLE = json.loads((Path(__file__).parent / "nws_alerts_sample.json").read_text())
NOW = datetime(2026, 9, 28, 12, 0)


def alert(id="a1", severity="Severe", references=(), **kwargs):
    defaults = dict(
        event="Flood Watch", urgency="Future", headline="Flood Watch issued...", nws_headline="",
        area="Potter; Randall", message_type="Alert", expires=None, ends=None,
    )
    defaults.update(kwargs)
    return WeatherAlert(id=id, severity=severity, references=tuple(references), **defaults)


def test_parse_real_nws_response():
    alerts = parse_alerts(SAMPLE)

    assert len(alerts) == 4
    fog = alerts[0]
    assert fog.event == "Dense Fog Advisory"
    assert fog.severity == "Moderate"
    assert fog.message_type == "Update"
    assert fog.nws_headline == "DENSE FOG ADVISORY WILL EXPIRE AT 11 AM CDT THIS MORNING"
    assert fog.references and fog.references[0].startswith("urn:oid:")


def test_parse_skips_test_and_exercise_messages():
    sample = json.loads(json.dumps(SAMPLE))
    sample["features"][0]["properties"]["status"] = "Test"

    assert len(parse_alerts(sample)) == 3


def test_severity_threshold():
    assert meets_severity(alert(severity="Extreme"), "Severe")
    assert meets_severity(alert(severity="Severe"), "Severe")
    assert not meets_severity(alert(severity="Moderate"), "Severe")
    assert not meets_severity(alert(severity="Unknown"), "Moderate")
    assert meets_severity(alert(severity="Unknown"), "Minor")


def test_speech_text_is_readable():
    text = speech_text(alert(nws_headline="FLOOD WATCH REMAINS IN EFFECT THROUGH TUESDAY"))

    assert text == "National Weather Service Flood Watch. For Potter, Randall. Flood watch remains in effect through tuesday."


def test_speech_text_keeps_time_zones_capitalized():
    text = speech_text(alert(area="", nws_headline="DENSE FOG ADVISORY WILL EXPIRE AT 11 AM CDT THIS MORNING"))

    assert text == "National Weather Service Flood Watch. Dense fog advisory will expire at 11 am CDT this morning."


def test_speech_text_skips_very_long_area_lists_and_falls_back_to_headline():
    text = speech_text(alert(area="; ".join(["County"] * 40), headline="Flood Watch issued today by NWS Amarillo TX"))

    assert text == "National Weather Service Flood Watch. Flood Watch issued today by NWS Amarillo TX."


def test_new_alert_is_announced_once():
    tracker = AlertTracker()

    assert tracker.update([alert()], NOW) == [alert()]
    assert tracker.update([alert()], NOW + timedelta(minutes=5)) == []


def test_reissued_update_of_an_announced_alert_is_not_repeated():
    tracker = AlertTracker()
    tracker.update([alert(id="a1")], NOW)

    update = alert(id="a2", references=["a1"], message_type="Update")
    assert tracker.update([update], NOW + timedelta(minutes=30)) == []
    assert tracker.was_announced("a2")


def test_below_threshold_alerts_are_ignored():
    tracker = AlertTracker()

    assert tracker.update([alert(severity="Minor")], NOW, minimum_severity="Severe") == []
    assert not tracker.was_announced("a1")


def test_repeat_interval_re_announces_ongoing_alerts():
    tracker = AlertTracker()
    tracker.update([alert()], NOW, repeat_every=timedelta(hours=1))

    assert tracker.update([alert()], NOW + timedelta(minutes=59), repeat_every=timedelta(hours=1)) == []
    assert tracker.update([alert()], NOW + timedelta(hours=1), repeat_every=timedelta(hours=1)) == [alert()]


def test_expired_alerts_are_forgotten():
    tracker = AlertTracker()
    tracker.update([alert()], NOW)
    tracker.update([], NOW + timedelta(hours=1))

    assert not tracker.was_announced("a1")
    assert tracker.update([alert()], NOW + timedelta(hours=2)) == [alert()]


def _feature(alert_id, geometry=None, zones=()):
    return {
        "id": alert_id,
        "geometry": geometry,
        "properties": {
            "id": alert_id, "status": "Actual", "event": "Tornado Warning", "severity": "Extreme",
            "areaDesc": "Somewhere", "affectedZones": list(zones),
        },
    }


SQUARE = {"type": "Polygon", "coordinates": [[[-73, 41], [-72, 41], [-72, 42], [-73, 41]]]}
ZONE_A = "https://api.weather.gov/zones/county/CTC003"
ZONE_B = "https://api.weather.gov/zones/forecast/CTZ002"


def test_alert_areas_use_polygon_or_zone_outlines():
    from wx.nws import alert_areas, missing_zones

    alerts = parse_alerts({"features": [
        _feature("poly", SQUARE, [ZONE_A]),
        _feature("zones", None, [ZONE_A, ZONE_B, "https://evil.example/zones/x"]),
        _feature("unknown", None, [ZONE_B]),
    ]})
    assert alerts[0].geometry == SQUARE
    assert alerts[1].zones == (ZONE_A, ZONE_B)
    assert missing_zones(alerts, {}) == [ZONE_A, ZONE_B]  # the polygon alert needs none of its own

    areas = alert_areas(alerts, {ZONE_A: SQUARE})
    assert [f["properties"]["id"] for f in areas["features"]] == ["poly", "zones"]
    assert areas["features"][1]["geometry"] == SQUARE

    both = alert_areas(alerts, {ZONE_A: SQUARE, ZONE_B: SQUARE})
    assert both["features"][1]["geometry"]["type"] == "GeometryCollection"
    assert len(both["features"]) == 3
