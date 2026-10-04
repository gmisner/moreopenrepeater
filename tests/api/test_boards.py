import dataclasses
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from controller.state_machine import RepeaterConfig

from api.app import create_app
from api.assets import AudioAssetStore
from api.boards import WIRING_FIELDS, alsa_card, apply_mixer, load_boards, preset_changes
from api.models import ConfigUpdateRequest
from api.service import RepeaterService
from playout.renderer import ClipRenderer

RATE = 8000


class FakeTTS:
    name = "fake"

    def synthesize(self, text, voice=""):
        return np.full(RATE, 0.1, dtype=np.float32), RATE


class FakeAmixer:
    def __init__(self, missing=()):
        self.calls = []
        self.missing = missing

    def __call__(self, args):
        if args[-1] == "scontents":
            return subprocess.CompletedProcess(args, 0, "", "")
        self.calls.append(args)
        control = args[5]
        if control in self.missing:
            return subprocess.CompletedProcess(args, 1, "", f"amixer: Unable to find simple control '{control}',0\n")
        return subprocess.CompletedProcess(args, 0, "", "")


def make_client(config=RepeaterConfig(callsign="W1AW"), amixer=None):
    tmp = Path(tempfile.mkdtemp())
    assets = AudioAssetStore(tmp / "audio")
    renderer = ClipRenderer(assets.path_for, tts=FakeTTS(), sample_rate=RATE)
    service = RepeaterService(config=config, clock=lambda: 0.0, renderer=renderer)
    amixer = amixer or FakeAmixer()
    app = create_app(
        service=service, start_background_tick=False, assets_store=assets, log_path=tmp / "t.log", renderer=renderer, run_mixer=amixer
    )
    return TestClient(app), service, amixer


def board(board_id):
    return next(b for b in load_boards() if b.id == board_id)


@pytest.mark.parametrize("preset", load_boards(), ids=lambda b: b.id)
def test_every_preset_sets_valid_config_fields(preset):
    config_fields = {f.name for f in dataclasses.fields(RepeaterConfig)}
    changes = {**preset.repeater, **(preset.link or {})}
    assert set(changes) <= config_fields
    ConfigUpdateRequest(**changes)  # pin ranges, polarities and sources the API would accept
    assert set(changes) <= WIRING_FIELDS


@pytest.mark.parametrize("preset", load_boards(), ids=lambda b: b.id)
def test_every_preset_either_wires_the_repeater_or_says_why_not(preset):
    assert preset.kind in ("usb", "pi")
    if preset.unsupported:
        assert not preset.repeater
    else:
        assert {"cos_source", "ptt_output"} <= set(preset.repeater)


def test_preset_ids_are_unique():
    ids = [b.id for b in load_boards()]
    assert len(ids) == len(set(ids))


def test_two_port_board_wires_the_link_radio_but_only_turns_it_on_with_devices():
    svx = board("svxlink-card")

    changes = preset_changes(svx, "Card 1 (hw:0,0)", "Card 1 (hw:0,0)")
    assert changes["link_radio_cos_gpio_pin"] == 18
    assert "link_radio_enabled" not in changes

    changes = preset_changes(svx, "Card 1 (hw:0,0)", "Card 1 (hw:0,0)", "Card 2 (hw:1,0)", "Card 2 (hw:1,0)")
    assert changes["link_radio_enabled"] is True
    assert changes["link_radio_input_device"] == "Card 2 (hw:1,0)"


def test_alsa_card_reads_the_card_number_from_a_portaudio_name():
    assert alsa_card("USB Audio Device: - (hw:2,0)") == 2
    assert alsa_card("default") is None
    assert alsa_card("") is None


def test_apply_mixer_reports_missing_controls_and_a_missing_amixer():
    amixer = FakeAmixer(missing=("Mic",))
    results = apply_mixer([1], (("Speaker", "80% unmute"), ("Mic", "18dB")), amixer)
    assert amixer.calls[0] == ["amixer", "-q", "-c", "1", "sset", "Speaker", "80%", "unmute"]
    assert results[0].error is None
    assert "Unable to find simple control" in results[1].error

    def no_amixer(args):
        raise FileNotFoundError(args[0])

    assert "alsa-utils" in apply_mixer([0], (("Speaker", "75%"),), no_amixer)[0].error


def test_boards_route_lists_presets_with_unsupported_ones_marked():
    client, *_ = make_client()
    listed = {b["id"]: b for b in client.get("/api/boards").json()}
    assert listed["dmk-uri"]["mixer"] == [["Speaker", "75%"], ["Mic", "18dB"]]
    assert listed["svxlink-basic"]["two_port"] is True
    assert listed["aioc"]["two_port"] is False
    assert listed["ics-2x"]["unsupported"]


def test_applying_a_usb_preset_sets_wiring_devices_and_mixer():
    client, service, amixer = make_client()

    response = client.post(
        "/api/boards/dmk-uri/apply", json={"input_device": "URI (hw:1,0)", "output_device": "URI (hw:1,0)"}
    )

    assert response.status_code == 200
    body = response.json()
    config = service.saved_config
    assert (config.cos_source, config.cos_polarity, config.ptt_output) == ("cm108", "low", "cm108")
    assert config.audio_enabled and config.audio_input_device == "URI (hw:1,0)"
    assert config.board_preset == "dmk-uri" == body["config"]["board_preset"]
    assert [c[2:] for c in amixer.calls] == [["-c", "1", "sset", "Speaker", "75%"], ["-c", "1", "sset", "Mic", "18dB"]]
    assert all(r["error"] is None for r in body["mixer"])
    assert body["mixer_skipped"] == []


def test_applying_a_two_port_preset_sets_levels_on_both_cards():
    client, service, amixer = make_client()

    client.post(
        "/api/boards/svxlink-basic/apply",
        json={
            "input_device": "A (hw:0,0)", "output_device": "A (hw:0,0)",
            "link_input_device": "B (hw:1,0)", "link_output_device": "B (hw:1,0)",
        },
    )

    config = service.saved_config
    assert (config.cos_gpio_pin, config.cos_polarity, config.ptt_gpio_pin) == (23, "high", 24)
    assert config.link_radio_enabled and (config.link_radio_cos_gpio_pin, config.link_radio_ptt_gpio_pin) == (25, 18)
    assert sorted({c[3] for c in amixer.calls}) == ["0", "1"]


def test_mixer_is_skipped_for_the_default_device_or_on_request():
    client, _, amixer = make_client()

    body = client.post("/api/boards/dmk-uri/apply", json={}).json()
    assert body["mixer_skipped"] == ["System default"] and amixer.calls == []

    body = client.post("/api/boards/dmk-uri/apply", json={"output_device": "URI (hw:1,0)", "set_mixer": False}).json()
    assert body["mixer"] == [] and amixer.calls == []


def test_unknown_and_unsupported_boards_are_refused():
    client, service, _ = make_client()
    assert client.post("/api/boards/nope/apply", json={}).status_code == 404
    response = client.post("/api/boards/ics-1x/apply", json={})
    assert response.status_code == 409
    assert "expander" in response.json()["detail"]
    assert service.saved_config.board_preset == ""


def test_editing_the_wiring_by_hand_forgets_the_preset():
    client, service, _ = make_client()
    client.post("/api/boards/dmk-uri/apply", json={})

    client.put("/api/config", json={"tx_gain_db": -3, "cos_source": "cm108"})
    assert service.saved_config.board_preset == "dmk-uri"  # unchanged wiring

    client.put("/api/config", json={"cos_source": "vox"})
    assert service.saved_config.board_preset == ""


def test_setup_wizard_flag_round_trips():
    client, service, _ = make_client()
    assert client.get("/api/config").json()["setup_wizard_done"] is False
    client.put("/api/config", json={"setup_wizard_done": True})
    assert service.saved_config.setup_wizard_done is True


def test_test_id_needs_live_audio_then_queues_the_id():
    client, service, _ = make_client()
    assert client.post("/api/audio/test-id").status_code == 409

    service.update_config(audio_enabled=True)
    response = client.post("/api/audio/test-id")

    assert response.status_code == 200
    service.tick()
    assert service.ptt_active
