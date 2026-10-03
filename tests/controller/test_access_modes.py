import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from api.app import create_app
from api.assets import AudioAssetStore
from api.service import RepeaterService
from controller.events import COSChanged, CTCSSChanged, RemoteKeyed, ToneBurst
from controller.state_machine import IDLE, RECEIVING, TONE_BURST_GRACE, RepeaterConfig, RepeaterController


def make(**overrides):
    defaults = dict(courtesy_tone_duration=0.2, hang_time=1.0, tot_duration=30.0, id_interval=1000.0)
    defaults.update(overrides)
    return RepeaterController(RepeaterConfig(**defaults), now=0.0)


def drop_to_idle(controller, now):
    """Unkey at `now`, then run the courtesy tone and hang time out."""
    controller.handle_event(COSChanged(active=False), now=now)
    for step in range(1, 40):
        controller.tick(now=now + step * 0.1)
    assert controller.state == IDLE
    return now + 4.0


def test_a_tone_detected_after_the_squelch_opened_still_brings_the_repeater_up():
    controller = make(require_ctcss_hz=100.0)
    controller.handle_event(COSChanged(active=True), now=0.0)
    assert controller.state == IDLE

    controller.handle_event(CTCSSChanged(tone_hz=100.0), now=0.8)

    assert controller.state == RECEIVING
    assert controller.carrier_present


def test_carrier_mode_wants_the_tone_on_every_transmission():
    controller = make(require_ctcss_hz=100.0)
    controller.handle_event(CTCSSChanged(tone_hz=100.0), now=0.0)
    controller.handle_event(COSChanged(active=True), now=0.1)
    controller.handle_event(COSChanged(active=False), now=1.0)
    controller.handle_event(CTCSSChanged(tone_hz=None), now=1.0)

    controller.handle_event(COSChanged(active=True), now=1.3)  # in the hang time, no tone

    assert controller.state != RECEIVING
    assert not controller.carrier_present


def test_ctcss_open_lets_carrier_alone_keep_using_it_until_it_drops():
    controller = make(require_ctcss_hz=100.0, access_mode="ctcss_open")
    controller.handle_event(COSChanged(active=True), now=0.0)
    assert controller.state == IDLE

    controller.handle_event(CTCSSChanged(tone_hz=100.0), now=0.5)
    assert controller.state == RECEIVING
    controller.handle_event(CTCSSChanged(tone_hz=None), now=1.0)
    assert controller.carrier_present  # the tone went away; the carrier still counts
    controller.handle_event(COSChanged(active=False), now=2.0)
    controller.tick(now=2.3)

    controller.handle_event(COSChanged(active=True), now=2.5)  # a toneless user in the hang time
    assert controller.state == RECEIVING

    now = drop_to_idle(controller, 3.0)
    controller.handle_event(COSChanged(active=True), now=now)
    assert controller.state == IDLE  # back to needing the tone


def test_a_tone_burst_opens_it_while_the_carrier_is_up():
    controller = make(access_mode="tone_burst")
    controller.handle_event(COSChanged(active=True), now=0.0)
    assert controller.state == IDLE
    assert not controller.carrier_present

    controller.handle_event(ToneBurst(), now=0.4)

    assert controller.state == RECEIVING


def test_a_burst_just_before_the_carrier_counts_but_not_an_old_one():
    controller = make(access_mode="tone_burst")
    controller.handle_event(ToneBurst(), now=0.0)
    controller.handle_event(COSChanged(active=True), now=1.0)
    assert controller.state == RECEIVING

    controller = make(access_mode="tone_burst")
    controller.handle_event(ToneBurst(), now=0.0)
    controller.tick(now=TONE_BURST_GRACE + 0.5)
    controller.handle_event(COSChanged(active=True), now=TONE_BURST_GRACE + 1.0)
    assert controller.state == IDLE


def test_after_a_burst_carrier_works_until_the_repeater_drops():
    controller = make(access_mode="tone_burst")
    controller.handle_event(COSChanged(active=True), now=0.0)
    controller.handle_event(ToneBurst(), now=0.4)
    controller.handle_event(COSChanged(active=False), now=10.0)  # past the burst grace
    controller.tick(now=10.3)

    controller.handle_event(COSChanged(active=True), now=10.5)
    assert controller.state == RECEIVING

    now = drop_to_idle(controller, 12.0)
    controller.handle_event(COSChanged(active=True), now=now)
    assert controller.state == IDLE


def test_a_link_bringing_it_up_opens_it_to_local_carrier_too():
    controller = make(access_mode="tone_burst")
    controller.handle_event(RemoteKeyed(node_id="2000", keyed=True), now=0.0)
    controller.handle_event(RemoteKeyed(node_id="2000", keyed=False), now=2.0)
    controller.tick(now=2.3)

    controller.handle_event(COSChanged(active=True), now=2.5)

    assert controller.state == RECEIVING


def test_bursts_are_ignored_unless_the_mode_wants_them():
    controller = make(require_ctcss_hz=100.0)
    controller.handle_event(COSChanged(active=True), now=0.0)
    controller.handle_event(ToneBurst(), now=0.4)
    assert controller.state == IDLE


def test_the_kerchunk_filter_still_applies_after_a_burst():
    controller = make(access_mode="tone_burst", kerchunk_delay=0.5)
    controller.handle_event(COSChanged(active=True), now=0.0)
    controller.handle_event(ToneBurst(), now=0.4)
    assert controller.state == IDLE
    controller.tick(now=1.0)
    assert controller.state == RECEIVING


def test_access_settings_through_the_api():
    tmp = Path(tempfile.mkdtemp())
    service = RepeaterService(config=RepeaterConfig())
    client = TestClient(create_app(
        service=service, start_background_tick=False, assets_store=AudioAssetStore(tmp / "audio"), log_path=tmp / "t.log"
    ))
    body = client.put(
        "/api/config", json={"access_mode": "tone_burst", "tone_burst_ms": 500, "rx_deemphasis": True, "tx_preemphasis": True}
    ).json()
    assert (body["access_mode"], body["tone_burst_ms"], body["rx_deemphasis"], body["tx_preemphasis"]) == (
        "tone_burst", 500, True, True
    )
    assert service.config.access_mode == "tone_burst"
    assert client.put("/api/config", json={"tone_burst_ms": 50}).status_code == 422
    assert client.put("/api/config", json={"access_mode": "whistle"}).status_code == 422
