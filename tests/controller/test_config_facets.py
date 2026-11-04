"""Facet round-trip: flat dict ↔ RepeaterConfig ↔ ConfigResponse."""
from __future__ import annotations

import dataclasses

from api.config_schema import config_response_from_repeater, repeater_from_config_response
from api.models import ConfigResponse, ConfigUpdateRequest
from controller.config import CONFIG_FIELDS, RepeaterConfig, facet_for_field
from controller.config.clear_fields import ALL_CLEAR_FIELDS, FACET_CLEAR_FIELDS
from controller.config.facets import FACET_CLASSES
from controller.config.facets.weather import WeatherConfig
from controller.config.repeater import FACET_FIELD_NAMES


def test_every_field_lives_in_exactly_one_facet():
    names = [n for _, names in FACET_FIELD_NAMES.items() for n in names]
    assert len(names) == len(set(names)) == len(CONFIG_FIELDS)
    assert set(names) == CONFIG_FIELDS


def test_facet_counts_match_inventory():
    counts = {name: len(names) for name, names in FACET_FIELD_NAMES.items()}
    assert counts == {
        "identity_timing": 38,
        "radio_audio": 33,
        "link_radio": 27,
        "remote_base": 8,
        "aprs": 17,
        "weather": 7,
        "monitor": 9,
        "autopatch": 12,
        "mailbox": 7,
        "net": 13,
        "gpio_fan": 8,
        "homeassistant": 2,
        "linking": 3,
        "backup": 4,
        "transcription": 3,
        "public_page": 4,
        "sensors": 3,
    }
    assert sum(counts.values()) == 198


def test_flat_dict_round_trip_preserves_values():
    original = RepeaterConfig(
        callsign="W1AW",
        hang_time=4.5,
        wx_lat=35.2,
        wx_lon=-101.8,
        aprs_enabled=True,
        mailbox_enabled=True,
    )
    flat = original.to_flat_dict()
    assert flat["callsign"] == "W1AW"
    assert flat["wx_lat"] == 35.2
    assert "weather" not in flat  # wire stays flat
    restored = RepeaterConfig.from_flat_dict(flat)
    assert restored == original
    assert restored.weather.wx_lat == 35.2
    assert restored.identity_timing.callsign == "W1AW"


def test_nested_facet_view_and_with_facet():
    base = RepeaterConfig(callsign="N0CALL")
    updated = base.with_facet(
        "weather",
        WeatherConfig(wx_alerts_enabled=True, wx_lat=40.0, wx_lon=-105.0, wx_min_severity="Moderate"),
    )
    assert updated.weather.wx_alerts_enabled is True
    assert updated.wx_lat == 40.0
    assert updated.callsign == "N0CALL"
    assert facet_for_field("wx_lat") == "weather"


def test_config_response_round_trip():
    config = RepeaterConfig(callsign="K1ABC", tot_duration=120.0, public_page_mode="anyone")
    response = config_response_from_repeater(config)
    assert isinstance(response, ConfigResponse)
    assert response.callsign == "K1ABC"
    assert set(response.model_dump()) == CONFIG_FIELDS
    back = repeater_from_config_response(response)
    assert back.to_flat_dict() == config.to_flat_dict()


def test_config_response_validates_flat_wire_blob():
    blob = RepeaterConfig(callsign="W1AW", aprs_symbol="/r").to_flat_dict()
    validated = ConfigResponse.model_validate(blob)
    assert validated.callsign == "W1AW"
    assert RepeaterConfig.from_flat_dict(validated.model_dump()).aprs_symbol == "/r"


def test_clear_fields_declared_on_facets_match_update_request():
    update_clears = {name.removeprefix("clear_") for name in ConfigUpdateRequest.model_fields if name.startswith("clear_")}
    assert set(ALL_CLEAR_FIELDS) == update_clears
    for facet, names in FACET_CLEAR_FIELDS.items():
        for name in names:
            assert facet_for_field(name) == facet


def test_replace_flat_updates_across_facets():
    config = RepeaterConfig(callsign="A", hang_time=3.0)
    next_cfg = config.replace_flat(callsign="B", wx_lat=1.5, hang_time=2.0)
    assert next_cfg.callsign == "B"
    assert next_cfg.hang_time == 2.0
    assert next_cfg.weather.wx_lat == 1.5


def test_facet_classes_defaults_match_repeater_config():
    composed = RepeaterConfig()
    for facet_name, cls in FACET_CLASSES:
        facet = getattr(composed, facet_name)
        assert dataclasses.asdict(facet) == {
            f.name: getattr(composed, f.name) for f in dataclasses.fields(cls)
        }
