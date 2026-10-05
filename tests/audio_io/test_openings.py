import numpy as np
import pytest

from audio_io.openings import OpeningTracker
from audio_io.processor import AudioProcessor, ProcessorSettings, level_db
from dsp.goertzel import ctcss_tone, dtmf_tone

RATE = 16000
BLOCK = 320


def tone(freq, seconds, amplitude=0.3):
    t = np.arange(int(seconds * RATE)) / RATE
    return (amplitude * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def silence(seconds):
    return np.zeros(int(seconds * RATE), dtype=np.float32)


def track(signal):
    tracker = OpeningTracker(RATE)
    tracker.start(-20.0, None)
    for i in range(0, len(signal) - BLOCK + 1, BLOCK):
        block = signal[i : i + BLOCK]
        tracker.feed(block, level_db(block), None, None)
    return tracker.finish()


@pytest.mark.parametrize("hz", [1000.0, 1477.0, 2175.0])
def test_a_steady_tone_is_found_and_counted_as_nearly_all_the_audio(hz):
    opening = track(tone(hz, 1.0))
    assert opening.strongest_hz == pytest.approx(hz, abs=5)
    assert opening.tone_share > 0.9
    assert opening.duration == pytest.approx(1.0, abs=0.02)
    assert opening.peak_db == pytest.approx(-13.5, abs=0.5)


def test_noise_is_spread_out():
    noise = (np.random.default_rng(3).standard_normal(RATE) * 0.1).astype(np.float32)
    assert track(noise).tone_share < 0.1


def test_a_quiet_tone_under_noise_still_stands_out():
    noise = (np.random.default_rng(4).standard_normal(2 * RATE) * 0.01).astype(np.float32)
    opening = track(noise + tone(1000.0, 2.0, amplitude=0.01))
    assert opening.strongest_hz == pytest.approx(1000, abs=5)


def test_silence_has_no_strongest_frequency():
    opening = track(silence(0.5))
    assert opening.strongest_hz is None and opening.tone_share == 0.0


def run(processor, signal):
    for i in range(0, len(signal) - BLOCK + 1, BLOCK):
        processor.process(signal[i : i + BLOCK])


def vox_processor(**settings):
    p = AudioProcessor(ProcessorSettings(RATE, vox_threshold_db=-40, vox_hold=0.4, **settings))
    openings = []
    p.on_opening = openings.append
    return p, openings


def test_the_processor_reports_each_opening_when_the_carrier_drops():
    p, openings = vox_processor()

    run(p, np.concatenate([silence(0.2), tone(1000, 1.0), silence(1.0), tone(600, 0.5), silence(1.0)]))

    assert len(openings) == 2
    first = openings[0]
    assert first.duration == pytest.approx(1.0 - 0.04 + 0.4, abs=0.03)  # opens after the attack, closes after the hold
    assert first.strongest_hz == pytest.approx(1000, abs=5)
    assert first.open_db == pytest.approx(-13.5, abs=0.5)
    assert first.after_tx is None
    assert openings[1].strongest_hz == pytest.approx(600, abs=5)


def test_an_opening_records_its_ctcss_tone_and_dtmf_digits():
    p, openings = vox_processor()
    voice = tone(800, 1.5) + ctcss_tone(100.0, RATE, int(1.5 * RATE), amplitude=0.05).astype(np.float32)
    digits = np.concatenate([dtmf_tone("4", RATE, int(0.2 * RATE)), silence(0.1), dtmf_tone("2", RATE, int(0.2 * RATE))])

    run(p, np.concatenate([voice, digits.astype(np.float32), tone(800, 0.5), silence(1.0)]))

    assert openings[0].ctcss_hz == 100.0
    assert openings[0].dtmf_digits == "42"


def test_an_opening_records_how_long_after_the_repeater_unkeyed_it_came():
    p, openings = vox_processor()
    p.set_ptt(True)
    run(p, silence(0.5))
    p.set_ptt(False)

    run(p, np.concatenate([silence(2.0), tone(1000, 0.5), silence(1.0)]))
    p.set_ptt(True)
    run(p, np.concatenate([tone(1000, 0.5), silence(1.0)]))

    assert openings[0].after_tx == pytest.approx(2.0 + 0.04, abs=0.03)
    assert openings[1].after_tx == 0.0
