import time

import numpy as np

from audio_io.engine import PRIME_BLOCKS, AudioEngine
from audio_io.processor import AudioProcessor, ProcessorSettings
from controller.events import COSChanged

RATE = 16000
BLOCK = 320


class FakeStream:
    device_rate = None  # None = run at the requested rate

    def __init__(self, sample_rate, block_size, input_queue, output_queue, device):
        self.sample_rate = self.device_rate or sample_rate
        self.block_size = round(block_size * self.sample_rate / sample_rate)
        self.input_queue = input_queue
        self.output_queue = output_queue
        self.device = device
        self.started = False
        self.active = False
        self.dropped_input_blocks = 0
        self.starved_output_blocks = 0

    def start(self):
        self.started = True
        self.active = True

    def stop(self):
        self.started = False
        self.active = False


class FakeStream48k(FakeStream):
    device_rate = 48000


def make_engine(stream_class=FakeStream, ptt_output=None, **kwargs):
    events, ptt = [], []
    streams = []

    def factory(**kw):
        streams.append(stream_class(**kw))
        return streams[-1]

    engine = AudioEngine(
        AudioProcessor(ProcessorSettings(RATE, vox_threshold_db=-30, vox_attack=0.02)),
        BLOCK,
        on_events=events.extend,
        ptt_output=ptt_output or ptt.append,
        stream_factory=factory,
        **kwargs,
    )
    return engine, events, ptt, streams


def test_process_one_emits_events_and_output():
    engine, events, ptt, _ = make_engine()

    engine.process_one(np.full(BLOCK, 0.2, dtype=np.float32))

    assert events == [COSChanged(active=True)]


def test_ptt_output_follows_transmitting():
    engine, _, ptt, _ = make_engine()
    engine.processor.play(np.full(BLOCK, 0.1, dtype=np.float32))

    engine.process_one(np.zeros(BLOCK, dtype=np.float32))
    engine.process_one(np.zeros(BLOCK, dtype=np.float32))

    assert ptt == [True, False]


def test_start_runs_the_worker_against_the_stream_queues():
    engine, events, _, streams = make_engine(input_device="BlackHole 2ch", output_device="Speakers")
    engine.start()
    try:
        stream = streams[0]
        assert stream.started and stream.device == ("BlackHole 2ch", "Speakers")
        assert stream.output_queue.qsize() == PRIME_BLOCKS
        stream.input_queue.put(np.full((BLOCK, 1), 0.2, dtype=np.float32))
        deadline = time.monotonic() + 2
        while not events and time.monotonic() < deadline:
            time.sleep(0.01)
        assert events == [COSChanged(active=True)]
    finally:
        engine.stop()
    assert not engine.running


def test_resamples_when_the_device_runs_at_another_rate():
    engine, events, _, streams = make_engine(stream_class=FakeStream48k)
    engine.start()
    engine._stop.set()  # drive process_one by hand instead of via the worker
    try:
        stream = streams[0]
        assert engine.device_sample_rate == 48000 and stream.block_size == 960
        while not stream.output_queue.empty():
            stream.output_queue.get_nowait()
        engine.processor.play(np.full(RATE, 0.25, dtype=np.float32))
        t = np.arange(960 * 10) / 48000
        loud = (0.3 * np.sin(2 * np.pi * 1000 * t)).astype(np.float32)
        for start in range(0, len(loud), 960):
            engine.process_one(loud[start : start + 960])

        assert COSChanged(active=True) in events
        out = [stream.output_queue.get_nowait() for _ in range(stream.output_queue.qsize())]
        assert len(out) >= 9 and all(block.shape == (960, 1) for block in out)
        assert abs(float(np.median(np.concatenate(out))) - 0.25) < 0.01  # the clip, upsampled
    finally:
        engine.stop()


def test_hardware_cos_is_polled():
    engine, events, _, _ = make_engine(cos_input=lambda: True)
    engine.processor.settings.cos_source = "external"
    engine.start()
    try:
        time.sleep(0.1)
        engine.process_one(np.zeros(BLOCK, dtype=np.float32))
        assert COSChanged(active=True) in events
    finally:
        engine.stop()


def test_each_new_glitch_is_reported_once():
    glitches = []
    engine, _, _, streams = make_engine(on_glitch=lambda name, count: glitches.append((name, count)))
    engine.start()
    stream = streams[0]

    engine.check_glitches()
    stream.starved_output_blocks = 2
    stream.input_overflows = 1
    engine.check_glitches()
    engine.check_glitches()
    stream.starved_output_blocks = 3
    engine.check_glitches()
    engine.stop()

    assert glitches == [("starved_output_blocks", 2), ("input_overflows", 1), ("starved_output_blocks", 1)]


def test_stream_death_callback_fires_when_stream_goes_inactive():
    deaths = []
    engine, _, _, streams = make_engine(on_stream_dead=lambda: deaths.append(True))
    engine.start()
    streams[0].active = False
    deadline = time.monotonic() + 2
    while not deaths and time.monotonic() < deadline:
        time.sleep(0.01)
    assert deaths == [True]
    assert not engine.running
    engine.stop()


def test_failed_ptt_assertion_is_not_reported_as_transmitting():
    def ptt(active):
        if active:
            raise OSError("key write failed")

    engine, _, _, _ = make_engine(ptt_output=ptt)
    engine.processor.play(np.full(BLOCK, 0.1, dtype=np.float32))
    engine.process_one(np.zeros(BLOCK, dtype=np.float32))

    assert not engine.transmitting
    assert "key write failed" in engine.ptt_error


def test_failed_unkey_retries_without_waiting_for_an_audio_block():
    writes = []

    def ptt(active):
        writes.append(active)
        if writes == [True, False]:
            raise OSError("unkey write failed")

    engine, _, _, _ = make_engine(ptt_output=ptt)
    engine.start()
    try:
        engine.processor.play(np.full(BLOCK, 0.1, dtype=np.float32))
        engine.process_one(np.zeros(BLOCK, dtype=np.float32))
        engine.process_one(np.zeros(BLOCK, dtype=np.float32))
        assert engine.transmitting
        assert "unkey write failed" in engine.ptt_error
        deadline = time.monotonic() + 2
        while engine.transmitting and time.monotonic() < deadline:
            time.sleep(0.01)
        assert writes == [True, False, False]
        assert not engine.transmitting
        assert engine.ptt_error is None
    finally:
        engine.stop()


def test_shutdown_unkeys_even_when_stopping_the_stream_fails():
    writes = []

    def ptt(active):
        writes.append(active)
        if writes == [True, False]:
            raise OSError("unkey write failed")

    class BrokenStop(FakeStream):
        def stop(self):
            raise OSError("stream already disconnected")

    engine, _, _, _ = make_engine(stream_class=BrokenStop, ptt_output=ptt)
    engine.start()
    engine.processor.play(np.full(BLOCK, 0.1, dtype=np.float32))
    engine.process_one(np.zeros(BLOCK, dtype=np.float32))
    engine.stop()

    assert writes == [True, False, False]
    assert not engine.transmitting
    assert engine.ptt_error is None
