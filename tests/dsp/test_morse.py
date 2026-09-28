from dsp.morse import morse_tone

SAMPLE_RATE = 8000
WPM = 20
DOT_SECONDS = 1.2 / WPM  # 0.06s at 20 WPM


def test_single_dot_character_has_expected_duration():
    result = morse_tone("E", wpm=WPM, tone_hz=700, sample_rate=SAMPLE_RATE)

    assert len(result) == round(1 * DOT_SECONDS * SAMPLE_RATE)


def test_tone_segment_is_not_silent():
    result = morse_tone("E", wpm=WPM, tone_hz=700, sample_rate=SAMPLE_RATE)

    assert len(result) > 0
    assert (result != 0).any()


def test_paris_paris_matches_standard_50_unit_calibration():
    # "PARIS" + a following word gap is the standard 50-unit calibration word;
    # two repetitions therefore take 50 + 43 = 93 units (the second has no
    # trailing gap since nothing follows it).
    result = morse_tone("PARIS PARIS", wpm=WPM, tone_hz=700, sample_rate=SAMPLE_RATE)

    expected_samples = round(93 * DOT_SECONDS * SAMPLE_RATE)
    assert abs(len(result) - expected_samples) <= 100


def test_unknown_characters_are_skipped_without_affecting_timing():
    with_junk = morse_tone("E~E", wpm=WPM, tone_hz=700, sample_rate=SAMPLE_RATE)
    without_junk = morse_tone("EE", wpm=WPM, tone_hz=700, sample_rate=SAMPLE_RATE)

    assert list(with_junk) == list(without_junk)


def test_empty_text_produces_empty_signal():
    result = morse_tone("", wpm=WPM, tone_hz=700, sample_rate=SAMPLE_RATE)

    assert len(result) == 0


def test_higher_wpm_produces_shorter_signal():
    slow = morse_tone("TEST", wpm=10, tone_hz=700, sample_rate=SAMPLE_RATE)
    fast = morse_tone("TEST", wpm=30, tone_hz=700, sample_rate=SAMPLE_RATE)

    assert len(fast) < len(slow)
