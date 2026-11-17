"""The transmit cap and watchdog coverage for both radios (issue #246, finding F5).

Simulation only. Nothing here shows what a real transmitter does.
"""
import test_link_radio as link_tests
import test_live_audio as live_tests

from api.runtime import RuntimeBuilder
from api.tx_limit import link_radio_transmit_limit, repeater_transmit_limit
from api.watchdog import Watchdog
from audio_io.engine import TX_LIMIT_MARGIN_SECONDS
from controller.config import RepeaterConfig


def test_the_main_limit_is_the_longest_controller_limit_plus_a_margin():
    config = RepeaterConfig(tot_duration=180.0, net_tot_duration=600.0, autopatch_max_call_seconds=900.0)
    assert repeater_transmit_limit(config) == 900.0 + TX_LIMIT_MARGIN_SECONDS
    assert repeater_transmit_limit(RepeaterConfig()) == 600.0 + TX_LIMIT_MARGIN_SECONDS  # net TOT default is the longest
    assert repeater_transmit_limit(RepeaterConfig(tot_duration=4000.0)) == 4000.0 + TX_LIMIT_MARGIN_SECONDS


def test_the_limit_is_always_above_every_controller_limit():
    for tot, net, call in [(30, 30, 30), (180, 3600, 180), (1, 1, 1)]:
        config = RepeaterConfig(tot_duration=tot, net_tot_duration=net, autopatch_max_call_seconds=call)
        assert repeater_transmit_limit(config) > max(tot, net, call)


def test_the_link_radio_limit_follows_its_own_timeout():
    assert link_radio_transmit_limit(RepeaterConfig(link_radio_timeout=90.0)) == 90.0 + TX_LIMIT_MARGIN_SECONDS


def test_the_main_engine_gets_the_limit_and_follows_config_changes():
    live, service, _clock, engines = live_tests.make_live()
    assert live.engine.max_transmit_seconds == repeater_transmit_limit(service.config)
    service.update_config(tot_duration=1000.0)
    assert len(engines) == 1  # no restart for a limit change
    assert live.engine.max_transmit_seconds == 1000.0 + TX_LIMIT_MARGIN_SECONDS


def test_the_link_radio_engine_gets_its_limit_and_it_is_watched_and_releasable():
    rig = link_tests.Rig(config=link_tests.CONFIG)
    engine = rig.link.engine
    assert engine is not None
    assert engine.max_transmit_seconds == link_radio_transmit_limit(rig.service.config)
    assert rig.link.progress() == engine.last_block_at  # something for the watchdog to watch
    rig.link.release_ptt()
    assert engine._ptt_inhibited  # released and inhibited, like the main radio


def test_an_unstarted_link_radio_cannot_stall_the_watchdog():
    rig = link_tests.Rig(config=RepeaterConfig(link_radio_enabled=False))
    assert rig.link.progress() is None
    rig.link.release_ptt()  # nothing to release; must not raise


def build_runtime(monkeypatch, main_fails=False):
    """A real runtime (in-memory stores, no devices) with the two release calls recorded."""
    watchdog = Watchdog()
    runtime = RuntimeBuilder(watchdog=watchdog, start_background_tick=False).build()
    released = []

    def main_release():
        released.append("main")
        if main_fails:
            raise RuntimeError("could not unkey")

    monkeypatch.setattr(runtime.live_audio, "release_ptt", main_release)
    monkeypatch.setattr(runtime.link_radio, "release_ptt", lambda: released.append("link"))
    return watchdog, released


def test_the_watchdog_watches_the_link_radio_and_releases_both_radios_on_a_stall(monkeypatch):
    watchdog, released = build_runtime(monkeypatch)
    assert "link radio audio engine" in [name for name, _ in watchdog._watched]
    watchdog.on_stall("controller")
    assert released == ["main", "link"]


def test_the_link_radio_is_still_unkeyed_if_the_main_radio_fails_to_release(monkeypatch):
    watchdog, released = build_runtime(monkeypatch, main_fails=True)
    try:
        watchdog.on_stall("controller")
    except RuntimeError:
        pass  # Watchdog.check logs a failing hook; calling it directly lets it through
    assert released == ["main", "link"]
