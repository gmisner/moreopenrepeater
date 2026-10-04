import numpy as np
import pytest

from dsp.goertzel import CTCSSDetector, DTMFDetector, _goertzel_magnitudes, ctcss_tone, dtmf_tone
from dsp.tones import CTCSS_TONES_HZ, DTMF_FREQUENCIES_BY_DIGIT

SAMPLE_RATE = 8000
BLOCK_SIZE = 320  # 40 ms


def _blocks(signal: np.ndarray, block_size: int = BLOCK_SIZE):
    for start in range(0, len(signal) - block_size + 1, block_size):
        yield signal[start : start + block_size]


def test_ctcss_detector_locks_onto_known_tone():
    tone_hz = 100.0
    assert tone_hz in CTCSS_TONES_HZ
    signal = ctcss_tone(tone_hz, SAMPLE_RATE, SAMPLE_RATE * 2, amplitude=1.0)

    detector = CTCSSDetector(sample_rate=SAMPLE_RATE)
    results = [detector.process(block) for block in _blocks(signal)]

    assert results[-1] == tone_hz
    assert results[0] is None  # window hasn't filled yet on the very first block


def test_ctcss_detector_unlocks_on_silence():
    tone_hz = 100.0
    tone_signal = ctcss_tone(tone_hz, SAMPLE_RATE, SAMPLE_RATE, amplitude=1.0)
    silence = np.zeros(SAMPLE_RATE)
    signal = np.concatenate([tone_signal, silence])

    detector = CTCSSDetector(sample_rate=SAMPLE_RATE)
    results = [detector.process(block) for block in _blocks(signal)]

    assert tone_hz in results
    assert results[-1] is None


def test_ctcss_detector_ignores_random_noise():
    rng = np.random.default_rng(seed=0)
    noise = rng.normal(scale=0.01, size=SAMPLE_RATE)

    detector = CTCSSDetector(sample_rate=SAMPLE_RATE)
    results = [detector.process(block) for block in _blocks(noise)]

    assert all(r is None for r in results)


def test_ctcss_detector_running_bins_match_a_fresh_window_analysis():
    rng = np.random.default_rng(seed=1)
    detector = CTCSSDetector(sample_rate=SAMPLE_RATE)
    signal = np.zeros(0)
    for size in [3000, 7, 320, 5000, 1, 320, 9000, 320, 123, 4567] * 3:
        block = rng.normal(scale=0.3, size=size) + ctcss_tone(131.8, SAMPLE_RATE, size, amplitude=0.2)
        detector.feed(block)
        signal = np.concatenate([signal, block])

    window = signal[-SAMPLE_RATE:]
    expected = _goertzel_magnitudes(window, SAMPLE_RATE, CTCSS_TONES_HZ)
    actual = np.abs(detector._bins) * 2.0 / SAMPLE_RATE
    np.testing.assert_allclose(actual, expected, rtol=1e-9, atol=1e-12)


@pytest.mark.parametrize("digit", ["1", "5", "9", "*", "#", "D"])
def test_dtmf_detector_recognizes_each_digit(digit):
    signal = dtmf_tone(digit, SAMPLE_RATE, SAMPLE_RATE, amplitude=1.0)

    detector = DTMFDetector(sample_rate=SAMPLE_RATE)
    results = [detector.process(block) for block in _blocks(signal)]

    assert digit in results


def test_dtmf_detector_reports_held_digit_once():
    signal = dtmf_tone("1", SAMPLE_RATE, SAMPLE_RATE, amplitude=1.0)

    detector = DTMFDetector(sample_rate=SAMPLE_RATE)
    results = [detector.process(block) for block in _blocks(signal)]

    assert results.count("1") == 1


def _tone_pair(row_hz, col_hz, row_amplitude, col_amplitude, sample_rate, seconds=1.0):
    t = np.arange(int(sample_rate * seconds)) / sample_rate
    return row_amplitude * np.sin(2 * np.pi * row_hz * t) + col_amplitude * np.sin(2 * np.pi * col_hz * t)


def test_dtmf_detector_hears_a_quiet_uneven_pair():
    """A star from a handheld into a CM108, as measured on a Pi: 941 Hz at
    0.052 and 1209 Hz at 0.032, under a little receiver noise."""
    rng = np.random.default_rng(seed=2)
    signal = _tone_pair(941, 1209, 0.052, 0.032, 16000) + rng.normal(scale=0.0003, size=16000)

    detector = DTMFDetector(sample_rate=16000, magnitude_threshold=0.01)
    results = [detector.process(block) for block in _blocks(signal, 320)]

    assert results.count("*") == 1


def test_dtmf_detector_reports_a_held_key_once_through_dropouts():
    """Over the air a held key's high tone dips for a block now and then, and
    a busy Pi's sound card can drop 40 ms of input."""
    signal = _tone_pair(941, 1209, 0.06, 0.035, 16000, seconds=1.5)
    for start in (0.3, 0.7):
        dip = slice(int(start * 16000), int(start * 16000) + 320)
        signal[dip] = _tone_pair(941, 1209, 0.05, 0.012, 16000, seconds=0.02)
    signal[int(1.1 * 16000) : int(1.1 * 16000) + 640] = 0  # the sound card filling a late input with silence

    detector = DTMFDetector(sample_rate=16000, magnitude_threshold=0.01)
    results = [detector.process(block) for block in _blocks(signal, 320)]

    assert results.count("*") == 1


def test_dtmf_detector_reports_digits_with_a_short_gap():
    gap = _tone_pair(941, 1209, 0.0003, 0.0003, 16000, 0.08)  # receiver noise, not digital silence
    signal = np.concatenate(
        [_tone_pair(941, 1209, 0.05, 0.03, 16000, 0.1), gap, _tone_pair(697, 1209, 0.05, 0.03, 16000, 0.1)]
    )

    detector = DTMFDetector(sample_rate=16000, magnitude_threshold=0.01)
    results = [detector.process(block) for block in _blocks(signal, 320)]

    assert [r for r in results if r] == ["*", "1"]


def test_dtmf_detector_hears_a_one_with_the_high_tone_9_db_down():
    """A 1 from a handheld over FM, as measured on a Pi: 697 Hz at 0.044, 1209 Hz at 0.015."""
    signal = _tone_pair(697, 1209, 0.044, 0.015, 16000)

    detector = DTMFDetector(sample_rate=16000, magnitude_threshold=0.01)
    results = [detector.process(block) for block in _blocks(signal, 320)]

    assert results.count("1") == 1


@pytest.mark.parametrize("digit", list("123A456B789C*0#D"))
def test_dtmf_detector_hears_every_digit_with_the_high_tone_9_5_db_down(digit):
    """A 9 from a handheld over FM, as measured on a Pi: 852 Hz at 0.023, 1477 Hz at
    0.0077. 1477 Hz sits halfway between two 50 Hz bins of a 20 ms block."""
    row_hz, col_hz = DTMF_FREQUENCIES_BY_DIGIT[digit]
    signal = _tone_pair(row_hz, col_hz, 0.023, 0.0077, 16000)

    detector = DTMFDetector(sample_rate=16000, magnitude_threshold=0.003)
    results = [detector.process(block) for block in _blocks(signal, 320)]

    assert results.count(digit) == 1


@pytest.mark.parametrize("row_amplitude, col_amplitude", [(0.1, 0.02), (0.02, 0.06)])
def test_dtmf_detector_rejects_a_pair_too_far_out_of_balance(row_amplitude, col_amplitude):
    signal = _tone_pair(697, 1209, row_amplitude, col_amplitude, 16000)

    detector = DTMFDetector(sample_rate=16000, magnitude_threshold=0.01)
    results = [detector.process(block) for block in _blocks(signal, 320)]

    assert all(r is None for r in results)


def test_dtmf_detector_ignores_noise_as_loud_as_a_digit():
    rng = np.random.default_rng(seed=3)
    noise = rng.normal(scale=0.05, size=16000 * 5)

    detector = DTMFDetector(sample_rate=16000, magnitude_threshold=0.01)
    results = [detector.process(block) for block in _blocks(noise, 320)]

    assert all(r is None for r in results)


def test_dtmf_detector_ignores_a_pair_buried_in_louder_audio():
    rng = np.random.default_rng(seed=4)
    signal = _tone_pair(941, 1209, 0.03, 0.03, 16000) + rng.normal(scale=0.1, size=16000)

    detector = DTMFDetector(sample_rate=16000, magnitude_threshold=0.01)
    results = [detector.process(block) for block in _blocks(signal, 320)]

    assert all(r is None for r in results)
