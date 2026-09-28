import numpy as np
import pytest

from audio_io.resample import StreamResampler


def sine(hz, rate, seconds, amplitude=0.5):
    t = np.arange(int(rate * seconds)) / rate
    return (amplitude * np.sin(2 * np.pi * hz * t)).astype(np.float32)


def run_in_blocks(resampler, signal, block):
    return np.concatenate([resampler.process(signal[i : i + block]) for i in range(0, len(signal), block)])


def dominant_hz(signal, rate):
    spectrum = np.abs(np.fft.rfft(signal * np.hanning(len(signal))))
    return np.fft.rfftfreq(len(signal), 1 / rate)[np.argmax(spectrum)]


@pytest.mark.parametrize("from_rate,to_rate", [(48000, 16000), (44100, 16000), (16000, 48000), (16000, 44100)])
def test_keeps_the_tone_and_the_length(from_rate, to_rate):
    signal = sine(1000, from_rate, 1.0)
    out = run_in_blocks(StreamResampler(from_rate, to_rate), signal, from_rate // 50)

    # Upsampling holds back the samples after the last input until more arrives.
    assert abs(len(out) - to_rate) <= np.ceil(to_rate / from_rate)
    settled = out[200:-200]
    assert abs(dominant_hz(settled, to_rate) - 1000) < 3
    assert 0.45 < np.max(np.abs(settled)) < 0.55


def test_block_size_does_not_change_the_result():
    signal = sine(700, 44100, 0.5)
    whole = StreamResampler(44100, 16000).process(signal)
    pieces = run_in_blocks(StreamResampler(44100, 16000), signal, 882)
    assert len(whole) == len(pieces)
    np.testing.assert_allclose(whole, pieces, atol=1e-5)


def test_downsampling_rejects_content_above_the_new_nyquist():
    alias_bait = sine(10000, 48000, 1.0)  # would fold to 6 kHz without filtering
    out = StreamResampler(48000, 16000).process(alias_bait)
    assert np.max(np.abs(out[200:])) < 0.02


def test_same_rate_is_passthrough():
    signal = sine(440, 16000, 0.1)
    np.testing.assert_array_equal(StreamResampler(16000, 16000).process(signal), signal)
