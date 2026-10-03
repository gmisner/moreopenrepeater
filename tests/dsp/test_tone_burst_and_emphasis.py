import numpy as np
import pytest

from dsp.emphasis import emphasis_kernel, preemphasis_gain
from dsp.goertzel import ToneBurstDetector

RATE = 8000
BLOCK = 160


def tone(freq, seconds, amplitude=0.3):
    t = np.arange(int(seconds * RATE)) / RATE
    return amplitude * np.sin(2 * np.pi * freq * t)


def heard(detector, signal):
    return [detector.process(signal[i : i + BLOCK]) for i in range(0, len(signal) - BLOCK + 1, BLOCK)]


def test_a_long_enough_burst_is_reported_once():
    results = heard(ToneBurstDetector(RATE, min_seconds=0.3), tone(1750, 1.0))
    assert results.count(True) == 1
    assert results.index(True) == 14  # the 15th 20 ms block makes 300 ms


def test_a_short_burst_isnt_reported():
    assert not any(heard(ToneBurstDetector(RATE, min_seconds=0.3), tone(1750, 0.25)))


def test_each_burst_is_reported_again_after_a_gap():
    signal = np.concatenate([tone(1750, 0.4), np.zeros(RATE // 5), tone(1750, 0.4)])
    assert heard(ToneBurstDetector(RATE, min_seconds=0.3), signal).count(True) == 2


@pytest.mark.parametrize("signal", [
    tone(1500, 1.0),
    tone(1750, 1.0, amplitude=0.005),
    tone(1750, 1.0) + tone(400, 1.0) + tone(900, 1.0) + tone(2600, 1.0),
])
def test_other_tones_quiet_tones_and_voice_like_audio_dont_count(signal):
    assert not any(heard(ToneBurstDetector(RATE, min_seconds=0.3), signal))


def test_a_burst_under_a_ctcss_tone_still_counts():
    assert any(heard(ToneBurstDetector(RATE, min_seconds=0.3), tone(1750, 1.0) + tone(100, 1.0, amplitude=0.1)))


def response_db(kernel, rate, freq):
    spectrum = np.abs(np.fft.rfft(kernel, 1 << 16))
    return 20 * np.log10(spectrum[int(round(freq * (1 << 16) / rate))])


@pytest.mark.parametrize("rate", [8000, 16000, 48000])
def test_emphasis_follows_six_db_per_octave_with_0_db_at_1_khz(rate):
    pre, de = emphasis_kernel(rate, pre=True), emphasis_kernel(rate, pre=False)
    for freq in (200, 300, 1000, 2000, 3000):
        ideal = 20 * np.log10(preemphasis_gain(freq))
        assert response_db(pre, rate, freq) == pytest.approx(ideal, abs=0.3)
        assert response_db(de, rate, freq) == pytest.approx(-ideal, abs=0.3)
    assert response_db(pre, rate, 1000) == pytest.approx(0, abs=0.1)
    assert response_db(pre, rate, 600) - response_db(pre, rate, 1200) == pytest.approx(-5.0, abs=0.6)
