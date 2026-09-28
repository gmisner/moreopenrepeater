import numpy as np

from audio_io.processor import SILENCE_DB, AudioProcessor, ProcessorSettings, level_db
from controller.events import COSChanged, CTCSSChanged, DTMFDigit
from dsp.goertzel import ctcss_tone, dtmf_tone

RATE = 16000
BLOCK = 320  # 20 ms


def blocks(signal):
    return [signal[i : i + BLOCK] for i in range(0, len(signal) - BLOCK + 1, BLOCK)]


def run(processor, signal):
    results = [processor.process(b) for b in blocks(signal)]
    events = [e for r in results for e in r.events]
    return results, events


def tone(freq, seconds, amplitude=0.3):
    t = np.arange(int(seconds * RATE)) / RATE
    return (amplitude * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def silence(seconds):
    return np.zeros(int(seconds * RATE), dtype=np.float32)


def test_level_db():
    assert level_db(np.zeros(BLOCK)) == SILENCE_DB
    assert round(level_db(np.full(BLOCK, 0.1))) == -20


def test_vox_opens_after_attack_and_closes_after_hold():
    p = AudioProcessor(ProcessorSettings(RATE, vox_threshold_db=-30, vox_attack=0.06, vox_hold=0.4))

    results, events = run(p, np.concatenate([silence(0.2), tone(1000, 1.0), silence(1.0)]))

    cos = [e for e in events if isinstance(e, COSChanged)]
    assert cos == [COSChanged(active=True), COSChanged(active=False)]
    opened = next(i for i, r in enumerate(results) if COSChanged(active=True) in r.events)
    closed = next(i for i, r in enumerate(results) if COSChanged(active=False) in r.events)
    assert opened == 10 + 2  # 200 ms of silence, then 3 blocks (60 ms) of signal
    assert closed - 60 == 19  # tone ends at block 60; closes once 400 ms of quiet has passed


def test_vox_ignores_quiet_noise():
    p = AudioProcessor(ProcessorSettings(RATE, vox_threshold_db=-30))
    noise = (np.random.default_rng(1).standard_normal(RATE) * 0.001).astype(np.float32)

    _, events = run(p, noise)

    assert events == []


def test_ctcss_is_detected_and_can_be_the_carrier_source():
    p = AudioProcessor(ProcessorSettings(RATE, cos_source="ctcss"))
    signal = ctcss_tone(100.0, RATE, int(2.5 * RATE), amplitude=0.1).astype(np.float32)

    _, events = run(p, signal)

    assert CTCSSChanged(tone_hz=100.0) in events
    assert COSChanged(active=True) in events
    assert p.ctcss_hz == 100.0


def test_dtmf_digits_are_reported_while_carrier_is_up():
    p = AudioProcessor(ProcessorSettings(RATE, vox_threshold_db=-40))
    digit = dtmf_tone("5", RATE, int(0.2 * RATE)).astype(np.float32)
    signal = np.concatenate([digit, silence(0.1), dtmf_tone("#", RATE, int(0.2 * RATE)).astype(np.float32)])

    _, events = run(p, signal)

    assert [e.digit for e in events if isinstance(e, DTMFDigit)] == ["5", "#"]


def test_receive_audio_is_repeated_only_while_keyed_and_repeating():
    p = AudioProcessor(ProcessorSettings(RATE))
    block = tone(1000, 0.02)

    assert not p.process(block).out.any()
    p.set_ptt(True)
    assert not p.process(block).out.any()  # keyed for hang time, but not repeating
    p.set_repeating(True)
    result = p.process(block)
    assert result.transmitting
    np.testing.assert_allclose(result.out, block, atol=1e-6)


def test_tx_gain_is_applied():
    p = AudioProcessor(ProcessorSettings(RATE, tx_gain_db=-6.0206))
    p.set_ptt(True)
    p.set_repeating(True)

    out = p.process(np.full(BLOCK, 0.5, dtype=np.float32)).out

    np.testing.assert_allclose(out, 0.25, atol=1e-4)


def test_clip_plays_and_holds_transmit_until_it_finishes():
    p = AudioProcessor(ProcessorSettings(RATE))
    p.play(np.full(BLOCK + 100, 0.2, dtype=np.float32))

    first = p.process(silence(0.02))
    second = p.process(silence(0.02))
    third = p.process(silence(0.02))

    assert first.transmitting and np.allclose(first.out, 0.2)
    assert second.transmitting and np.allclose(second.out[:100], 0.2) and not second.out[100:].any()
    assert not third.transmitting
    assert not p.playing


def test_external_cos_source():
    p = AudioProcessor(ProcessorSettings(RATE, cos_source="external"))

    p.set_external_cos(True)
    assert p.process(silence(0.02)).events == [COSChanged(active=True)]
