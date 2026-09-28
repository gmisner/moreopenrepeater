import numpy as np

from audio_io.patch import MAX_PHONE_SECONDS, MAX_RADIO_BLOCKS, PatchAudio
from audio_io.processor import AudioProcessor, ProcessorSettings

RATE = 16000
BLOCK = 320


def block(value):
    return np.full(BLOCK, value, dtype=np.float32)


def test_radio_audio_goes_to_the_phone_only_while_a_user_transmits():
    patch = PatchAudio(RATE)
    patch.exchange(block(0.5), carrier=True)
    patch.exchange(block(0.5), carrier=False)
    assert np.all(patch.take_radio() == 0.5)
    assert np.all(patch.take_radio() == 0)
    assert patch.take_radio() is None


def test_a_slow_phone_sender_drops_old_radio_blocks_instead_of_lagging():
    patch = PatchAudio(RATE)
    for i in range(10):
        patch.exchange(block(i / 10), carrier=True)
    sent = []
    while (b := patch.take_radio()) is not None:
        sent.append(round(float(b[0]), 1))
    assert sent == [0.7, 0.8, 0.9][-MAX_RADIO_BLOCKS:]


def test_phone_audio_waits_for_a_cushion_then_plays_in_order():
    patch = PatchAudio(RATE)
    patch.add_phone(np.arange(BLOCK, dtype=np.float32))
    assert not patch.exchange(block(0), carrier=False).any()  # 20 ms isn't enough yet
    patch.add_phone(np.arange(BLOCK, 4 * BLOCK, dtype=np.float32))
    out = np.concatenate([patch.exchange(block(0), carrier=False) for _ in range(4)])
    assert np.array_equal(out, np.arange(4 * BLOCK))


def test_running_dry_pads_with_silence_and_rebuilds_the_cushion():
    patch = PatchAudio(RATE)
    patch.add_phone(np.ones(4 * BLOCK + 100, dtype=np.float32))
    outs = [patch.exchange(block(0), carrier=False) for _ in range(5)]
    assert outs[4][:100].all() and not outs[4][100:].any()
    patch.add_phone(np.ones(BLOCK, dtype=np.float32))
    assert not patch.exchange(block(0), carrier=False).any()


def test_a_phone_backlog_is_trimmed():
    patch = PatchAudio(RATE)
    for _ in range(100):
        patch.add_phone(np.ones(BLOCK, dtype=np.float32))
    assert patch._queued <= MAX_PHONE_SECONDS * RATE


def test_processor_transmits_phone_audio_only_with_ptt():
    processor = AudioProcessor(ProcessorSettings(sample_rate=RATE))
    patch = PatchAudio(RATE)
    processor.set_patch(patch)
    for _ in range(5):
        patch.add_phone(np.full(BLOCK, 0.25, dtype=np.float32))
    assert not processor.process(np.zeros(BLOCK)).out.any()
    processor.set_ptt(True)
    result = processor.process(np.zeros(BLOCK))
    assert result.transmitting and np.allclose(result.out, 0.25)
    processor.set_patch(None)
    assert not processor.process(np.zeros(BLOCK)).out.any()
