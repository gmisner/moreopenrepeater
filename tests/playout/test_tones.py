import numpy as np
import pytest

from playout.tones import COURTESY_TONE_STYLES, SCALED_STYLES, courtesy_tone, parse_custom_tone, timeout_tone


@pytest.mark.parametrize("style", SCALED_STYLES)
def test_every_scaled_courtesy_style_matches_the_configured_duration(style):
    samples = courtesy_tone(style, 0.5, 8000)
    assert abs(len(samples) - 4000) <= 2
    assert np.max(np.abs(samples)) <= 0.31


@pytest.mark.parametrize("style", COURTESY_TONE_STYLES)
def test_courtesy_tones_fade_in_and_out_to_avoid_clicks(style):
    samples = courtesy_tone(style, 0.3, 8000)
    assert abs(samples[0]) < 0.01
    assert abs(samples[-1]) < 0.01
    assert 0 < np.max(np.abs(samples)) <= 0.31


def test_new_styles_sound_different():
    rendered = [courtesy_tone(style, 0.3, 8000).tobytes() for style in COURTESY_TONE_STYLES]
    assert len(set(rendered)) == len(COURTESY_TONE_STYLES)


def test_cw_letters_play_at_the_cw_speed():
    # K is dah dit dah: 3 + 1 + 1 + 1 + 3 units of 1.2 / wpm seconds.
    assert abs(len(courtesy_tone("cw_k", 0.2, 8000, cw_wpm=20)) - 9 * 0.06 * 8000) <= 6
    assert abs(len(courtesy_tone("cw_t", 0.2, 8000, cw_wpm=24)) - 3 * 0.05 * 8000) <= 2
    assert len(courtesy_tone("cw_r", 0.2, 8000, cw_wpm=10)) > len(courtesy_tone("cw_r", 0.2, 8000, cw_wpm=20))


def test_a_custom_tone_is_as_long_as_its_parts():
    samples = courtesy_tone("custom", 0.2, 8000, custom="1000:100 0:50 1500:150")
    assert len(samples) == 2400
    assert np.all(samples[800:1200] == 0)


def test_a_bad_custom_tone_falls_back_to_the_default():
    assert np.array_equal(courtesy_tone("custom", 0.2, 8000, custom="nope"), courtesy_tone("custom", 0.2, 8000))


def test_parse_custom_tone():
    assert parse_custom_tone(" 880:100  0:40 1320:100 ") == [(880, 100), (0, 40), (1320, 100)]
    for bad in ("", "880", "880:10", "880:1001", "50:100", "3001:100", "0:100", "1:1 2:2 3:3 4:4 5:5", "a:100"):
        with pytest.raises(ValueError):
            parse_custom_tone(bad)


def test_unknown_style_falls_back_to_a_plain_beep():
    assert np.array_equal(courtesy_tone("nope", 0.2, 8000), courtesy_tone("beep", 0.2, 8000))


def test_timeout_tone_is_three_quarter_second_tones():
    assert len(timeout_tone(8000)) == 6000
