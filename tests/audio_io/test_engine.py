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
        self.dropped_input_blocks = 0
        self.starved_output_blocks = 0

    def start(self):
        self.started = True

    def stop(self):
        self.started = False


class FakeStream48k(FakeStream):
    device_rate = 48000


def make_engine(stream_class=FakeStream, **kwargs):
    events, ptt = [], []
    streams = []

    def factory(**kw):
        streams.append(stream_class(**kw))
        return streams[-1]

    engine = AudioEngine(
        AudioProcessor(ProcessorSettings(RATE, vox_threshold_db=-30, vox_attack=0.02)),
        BLOCK,
        on_events=events.extend,
        ptt_output=ptt.append,
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
