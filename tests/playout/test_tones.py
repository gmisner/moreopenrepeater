import numpy as np
import pytest

from playout.tones import COURTESY_TONE_STYLES, courtesy_tone, timeout_tone


@pytest.mark.parametrize("style", COURTESY_TONE_STYLES)
def test_every_courtesy_style_matches_the_configured_duration(style):
    samples = courtesy_tone(style, 0.5, 8000)
    assert abs(len(samples) - 4000) <= 2
    assert np.max(np.abs(samples)) <= 0.31


@pytest.mark.parametrize("style", COURTESY_TONE_STYLES)
def test_courtesy_tones_fade_in_and_out_to_avoid_clicks(style):
    samples = courtesy_tone(style, 0.3, 8000)
    assert abs(samples[0]) < 0.01
    assert abs(samples[-1]) < 0.01


def test_unknown_style_falls_back_to_a_plain_beep():
    assert np.array_equal(courtesy_tone("nope", 0.2, 8000), courtesy_tone("beep", 0.2, 8000))


def test_timeout_tone_is_three_quarter_second_tones():
    assert len(timeout_tone(8000)) == 6000
