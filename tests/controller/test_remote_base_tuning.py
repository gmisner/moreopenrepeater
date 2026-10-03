import pytest

from controller.events import RunAction
from controller.link_radio import IDLE_ID_SECONDS, LinkIdle, LinkPlay, LinkRadioController, LinkRadioSettings
from controller.macros import CODE_TIMEOUT, DTMFCommandDecoder, Macro
from controller.remote_base import (
    in_ranges,
    parse_frequency,
    parse_ranges,
    parse_tone,
    spoken_mhz,
    summary,
    transmit_mhz,
)


def test_parse_frequency():
    assert parse_frequency("146*52") == 146.52
    assert parse_frequency("52*525") == 52.525
    assert parse_frequency("14652") == 146.52
    assert parse_frequency("446125") == 446.125
    assert parse_frequency("146") == 146.0
    assert parse_frequency("146*") == 146.0
    for bad in ("", "14", "1*4*6", "*52"):
        assert parse_frequency(bad) is None


def test_parse_tone():
    assert parse_tone("1000") == 100.0
    assert parse_tone("885") == 88.5
    assert parse_tone("100*0") == 100.0
    assert parse_tone("0") == 0.0
    assert parse_tone("1001") is None  # not a standard tone
    assert parse_tone("12") is None


def test_ranges_and_shift():
    ranges = parse_ranges("144-148, 420-450")
    assert in_ranges(146.52, ranges) and not in_ranges(222.1, ranges)
    assert transmit_mhz(146.94, "minus", None) == pytest.approx(146.34)
    assert transmit_mhz(444.0, "plus", None) == pytest.approx(449.0)
    assert transmit_mhz(147.3, "plus", 0.5) == pytest.approx(147.8)
    assert transmit_mhz(146.52, "simplex", None) == 146.52
    with pytest.raises(ValueError):
        parse_ranges("148-144")
    with pytest.raises(ValueError):
        parse_ranges("two meters")


def test_spoken():
    assert spoken_mhz(146.52) == "146.52"
    assert spoken_mhz(146.0) == "146.0"
    assert spoken_mhz(446.125) == "446.125"
    assert summary(146.94, "minus", 100.0) == "146.94, minus offset, tone 100.0"
    assert summary(146.52, "simplex", None) == "146.52, simplex, no tone"


def test_decoder_collects_keyed_digits_until_pound():
    decoder = DTMFCommandDecoder([Macro("*41", "tune", action="remote_tune"), Macro("*1", "time", action="time")])
    results = [decoder.handle_digit(d, 0.0) for d in "*41146*52#"]
    assert results[:-1] == [None] * 9
    assert results[-1] == RunAction("remote_tune", "146*52", "*41")
    assert decoder.handle_digit("*", 1.0) is None
    assert decoder.handle_digit("1", 1.0) == RunAction("time", "", "*1")


def test_decoder_drops_an_abandoned_or_garbled_entry():
    decoder = DTMFCommandDecoder([Macro("*41", "tune", action="remote_tune")])
    for d in "*41146":
        decoder.handle_digit(d, 0.0)
    decoder.tick(CODE_TIMEOUT + 1)
    assert [decoder.handle_digit(d, CODE_TIMEOUT + 2) for d in "52#"] == [None, None, None]
    for d in "*41146A":
        decoder.handle_digit(d, 100.0)
    assert decoder.handle_digit("#", 100.0) is None


def test_idle_remote_base_turns_off_once():
    controller = LinkRadioController(LinkRadioSettings(idle_off=600.0, id_interval=1000.0))
    assert controller.update(0.0, sending=False, receiving=False) == []
    assert controller.update(599.0, sending=False, receiving=True) == []
    assert controller.update(1198.0, sending=False, receiving=False) == []
    assert controller.update(1199.0, sending=False, receiving=False) == [LinkIdle()]
    assert controller.update(1300.0, sending=False, receiving=False) == []


def test_idle_sends_an_owed_id_before_turning_off():
    controller = LinkRadioController(LinkRadioSettings(idle_off=60.0, id_interval=600.0, courtesy_tone=False))
    controller.update(0.0, sending=True, receiving=False)
    controller.update(1.0, sending=False, receiving=False)
    controller.update(2.0, sending=False, receiving=False)
    assert controller.update(62.0, sending=False, receiving=False) == [LinkPlay("id")]
    assert controller.update(62.0 + IDLE_ID_SECONDS - 1, sending=False, receiving=False) == []
    assert controller.update(62.0 + IDLE_ID_SECONDS, sending=False, receiving=False) == [LinkIdle()]


def test_link_mode_never_idles():
    controller = LinkRadioController(LinkRadioSettings())
    controller.update(0.0, sending=False, receiving=False)
    assert controller.update(100_000.0, sending=False, receiving=False) == []
