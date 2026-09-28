import numpy as np
import pytest

from dsp.goertzel import CTCSSDetector, DTMFDetector, ctcss_tone, dtmf_tone
from dsp.tones import CTCSS_TONES_HZ

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
