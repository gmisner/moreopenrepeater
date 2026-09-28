import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from controller.state_machine import RepeaterConfig
from link.aprs_packet import parse_packet

from api.app import aprs_beacon_packet, create_app
from api.assets import AudioAssetStore
from api.service import RepeaterService

LOCATED = dict(aprs_callsign="W1AW-R", aprs_lat=41.7, aprs_lon=-72.7)


def make_client():
    service = RepeaterService(config=RepeaterConfig(), clock=lambda: 0.0)
    tmp_dir = Path(tempfile.mkdtemp())
    app = create_app(
        service=service,
        start_background_tick=False,
        assets_store=AudioAssetStore(tmp_dir / "audio"),
        log_path=tmp_dir / "test.log",
    )
    return TestClient(app), service


def test_beacon_uses_the_repeater_symbol_by_default():
    packet = aprs_beacon_packet(RepeaterConfig(**LOCATED, aprs_comment="Hartford"))

    assert packet == "!4142.00N/07242.00WrHartford"
    decoded = parse_packet(f"W1AW-R>APRS,TCPIP*:{packet}")
    assert decoded is not None
    assert (decoded.symbol_table, decoded.symbol_code, decoded.comment) == ("/", "r", "Hartford")


def test_beacon_honors_an_overlay_symbol():
    packet = aprs_beacon_packet(RepeaterConfig(**LOCATED, aprs_symbol="D&"))

    assert packet == "!4142.00ND07242.00W&"


def test_beacon_prefixes_the_frequency_and_falls_back_to_the_required_tone():
    config = RepeaterConfig(
        **LOCATED, aprs_comment="Hartford", aprs_frequency_mhz=146.94, aprs_offset_mhz=-0.6, require_ctcss_hz=107.2
    )

    assert aprs_beacon_packet(config).endswith("r146.940MHz T107 -060 Hartford")


def test_explicit_beacon_tone_overrides_the_required_tone():
    config = RepeaterConfig(**LOCATED, aprs_frequency_mhz=442.44, aprs_tone_hz=88.5, require_ctcss_hz=100.0)

    assert aprs_beacon_packet(config).endswith("r442.440MHz T088")


def test_unlocated_beacon_is_a_status_with_the_frequency():
    config = RepeaterConfig(aprs_callsign="W1AW-R", aprs_frequency_mhz=146.94, aprs_comment="Hartford")

    assert aprs_beacon_packet(config) == ">146.940MHz Hartford"


def test_config_accepts_and_clears_beacon_fields():
    client, service = make_client()

    response = client.put(
        "/api/config",
        json={"aprs_symbol": "/#", "aprs_frequency_mhz": 146.94, "aprs_offset_mhz": -0.6, "aprs_tone_hz": 100},
    )
    assert response.status_code == 200
    assert response.json()["aprs_symbol"] == "/#"
    assert service.config.aprs_offset_mhz == -0.6

    response = client.put("/api/config", json={"clear_aprs_frequency_mhz": True, "clear_aprs_tone_hz": True})
    assert response.json()["aprs_frequency_mhz"] is None
    assert response.json()["aprs_tone_hz"] is None


def test_config_rejects_bad_beacon_fields():
    client, _ = make_client()

    for bad in ({"aprs_symbol": "r"}, {"aprs_symbol": "xr"}, {"aprs_offset_mhz": 12}, {"aprs_frequency_mhz": 5}):
        assert client.put("/api/config", json=bad).status_code == 422, bad
