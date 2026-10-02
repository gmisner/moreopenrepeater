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
    p = AudioProcessor(ProcessorSettings(RATE, dtmf_mute=False))
    block = tone(1000, 0.02)

    assert not p.process(block).out.any()
    p.set_ptt(True)
    assert not p.process(block).out.any()  # keyed for hang time, but not repeating
    p.set_repeating(True)
    result = p.process(block)
    assert result.transmitting
    np.testing.assert_allclose(result.out, block, atol=1e-6)


def test_tx_gain_is_applied():
    p = AudioProcessor(ProcessorSettings(RATE, tx_gain_db=-6.0206, dtmf_mute=False))
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


def magnitude_at(signal, freq):
    t = np.arange(len(signal)) / RATE
    return float(np.abs(np.dot(signal, np.exp(-2j * np.pi * freq * t))) * 2 / len(signal))


def test_ctcss_encode_is_added_while_transmitting_and_is_phase_continuous():
    p = AudioProcessor(ProcessorSettings(RATE, tx_ctcss_hz=100.0, tx_ctcss_level_db=-20))
    p.set_ptt(True)

    out = np.concatenate([r.out for r in run(p, silence(1.0))[0]])

    assert abs(magnitude_at(out, 100.0) - 0.1) < 0.005
    # No clicks: a clean sine never jumps more than one sample's worth.
    assert np.max(np.abs(np.diff(out))) < 0.1 * 2 * np.pi * 100 / RATE * 1.01


def test_no_ctcss_encode_when_not_transmitting():
    p = AudioProcessor(ProcessorSettings(RATE, tx_ctcss_hz=100.0))
    out = np.concatenate([r.out for r in run(p, silence(0.5))[0]])
    assert not out.any()


def test_ctcss_encode_strips_the_received_tone_from_repeated_audio():
    p = AudioProcessor(ProcessorSettings(RATE, vox_threshold_db=-40, tx_ctcss_hz=131.8))
    p.set_ptt(True)
    p.set_repeating(True)
    received = tone(1000, 1.0, 0.3) + ctcss_tone(88.5, RATE, RATE, amplitude=0.1).astype(np.float32)

    out = np.concatenate([r.out for r in run(p, received)[0]])[RATE // 4 :]

    assert magnitude_at(out, 88.5) < 0.005  # the user's tone is gone
    assert abs(magnitude_at(out, 1000) - 0.3) < 0.02  # voice passes
    assert abs(magnitude_at(out, 131.8) - 0.1) < 0.01  # the repeater's tone is there


def test_link_radio_processor_never_repeats_its_own_receiver():
    p = AudioProcessor(ProcessorSettings(RATE, cos_source="external", local_repeat=False))
    p.set_external_cos(True)
    p.set_repeating(True)
    p.set_ptt(True)

    result = p.process(tone(1000, 0.02))

    assert result.transmitting and not result.out.any()
    assert p.repeating_voice



def test_port_gets_only_repeated_voice_and_its_audio_is_transmitted():
    from audio_io.patch import LinkAudio

    p = AudioProcessor(ProcessorSettings(RATE, cos_source="external", dtmf_mute=False))
    port = LinkAudio(RATE)
    p.set_port(port)
    p.set_external_cos(True)
    p.process(tone(1000, 0.02))
    assert port.take_radio() is None  # carrier, but the controller isn't repeating it

    p.set_repeating(True)
    p.process(tone(1000, 0.02))
    assert port.take_radio().any()

    port.add_phone(np.full(BLOCK * 4, 0.1, dtype=np.float32))
    p.set_ptt(True)
    out = p.process(np.zeros(BLOCK, dtype=np.float32)).out
    np.testing.assert_allclose(out, 0.1, atol=1e-6)


def dtmf_energy(signal):
    return max(magnitude_at(signal, 770), magnitude_at(signal, 1336))


def repeating_processor(**settings):
    p = AudioProcessor(ProcessorSettings(RATE, cos_source="external", **settings))
    p.set_external_cos(True)
    p.set_repeating(True)
    p.set_ptt(True)
    return p


def transmitted(p, signal):
    return np.concatenate([p.process(b).out for b in blocks(signal)])


def test_dtmf_digits_are_not_retransmitted_but_the_voice_around_them_is():
    p = repeating_processor()
    voice = tone(400, 0.5)
    digit = dtmf_tone("5", RATE, int(0.2 * RATE)).astype(np.float32)

    results = [p.process(b) for b in blocks(np.concatenate([voice, digit, voice, silence(0.1)]))]
    out = np.concatenate([r.out for r in results])

    assert [e.digit for r in results for e in r.events if isinstance(e, DTMFDigit)] == ["5"]
    assert dtmf_energy(out) < 0.01
    assert magnitude_at(out[: int(0.4 * RATE)], 400) > 0.05
    assert magnitude_at(out[int(0.8 * RATE) :], 400) > 0.05


def test_a_digit_at_the_very_start_of_a_transmission_is_muted_from_its_first_block():
    p = repeating_processor()
    digit = dtmf_tone("#", RATE, int(0.2 * RATE)).astype(np.float32)

    out = transmitted(p, np.concatenate([digit, silence(0.2)]))

    assert not out.any()


def test_dtmf_passes_when_muting_is_off():
    p = repeating_processor(dtmf_mute=False)
    digit = dtmf_tone("5", RATE, int(0.2 * RATE)).astype(np.float32)

    out = transmitted(p, digit)

    assert dtmf_energy(out) > 0.05


def test_dtmf_is_muted_in_recordings_and_what_the_links_get():
    from audio_io.patch import LinkAudio

    p = repeating_processor()
    link = LinkAudio(RATE)
    p.set_link(link)
    p.start_capture(5.0)
    digit = dtmf_tone("7", RATE, int(0.2 * RATE)).astype(np.float32)

    transmitted(p, np.concatenate([tone(400, 0.2), digit, tone(400, 0.2)]))

    offered = []
    while (b := link.take_radio()) is not None:
        offered.append(b)
    assert dtmf_energy(p.stop_capture()) < 0.01
    assert offered and dtmf_energy(np.concatenate(offered)) < 0.01


def test_repeat_audio_is_delayed_three_blocks_for_dtmf_muting():
    p = repeating_processor()

    outs = [p.process(b).out for b in blocks(tone(400, 0.1))]

    assert [bool(o.any()) for o in outs] == [False, False, False, True, True]


def test_squelch_tail_is_cut_when_the_carrier_drops():
    p = repeating_processor(dtmf_mute=False, squelch_tail_ms=100)
    signal = blocks(np.concatenate([tone(400, 0.4), (np.random.default_rng(2).standard_normal(int(0.1 * RATE)) * 0.3).astype(np.float32)]))
    outs = []
    for i, block in enumerate(signal):
        if i == len(signal) - 1:
            p.set_external_cos(False)  # squelch closes after the noise burst
        outs.append(p.process(block).out)
    outs += [p.process(np.zeros(BLOCK, dtype=np.float32)).out for _ in range(6)]
    out = np.concatenate(outs)

    noise_starts = int(0.4 * RATE) + 5 * BLOCK  # the 5-block (100 ms) delay
    assert np.abs(out[noise_starts:]).max() == 0
    assert magnitude_at(out[5 * BLOCK : noise_starts - BLOCK], 400) > 0.05


def test_transmit_delay_keys_up_first_and_clips_wait_for_it():
    p = AudioProcessor(ProcessorSettings(RATE, tx_delay_ms=50))
    p.play(np.full(BLOCK * 2, 0.5, dtype=np.float32))

    results = [p.process(np.zeros(BLOCK, dtype=np.float32)) for _ in range(6)]

    assert [r.transmitting for r in results] == [True] * 5 + [False]
    assert [bool(r.out.any()) for r in results] == [False, False, False, True, True, False]


def test_transmit_delay_only_at_key_up():
    p = repeating_processor(dtmf_mute=False, tx_delay_ms=40)

    outs = [p.process(b).out for b in blocks(tone(400, 0.2))]

    assert [bool(o.any()) for o in outs] == [False, False] + [True] * 8
