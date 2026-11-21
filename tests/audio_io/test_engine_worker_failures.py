"""A failure in the audio step must not leave the transmitter keyed or stop the transmit cap (review finding).

These run the real worker thread (`AudioEngine.start`), because the bug was in how `_work` and `process_one`
fit together: an exception in the processor or the tap skipped the only place PTT and the cap clock are
updated. Simulation only: nothing here shows what a real transmitter or sound card does.
"""
import time

import numpy as np
import pytest

from audio_io import engine as engine_module
from audio_io.engine import KEYED_FAILURE_LIMIT
from test_engine import BLOCK, make_engine

SILENCE = np.zeros((BLOCK, 1), dtype=np.float32)


class Worker:
    """An engine with its real worker running, fed one block at a time, each waited for."""

    def __init__(self, **kwargs):
        self.engine, self.events, self.ptt, self.streams = make_engine(**kwargs)
        self.processed = 0
        real = self.engine.process_one

        def counted(block):
            try:
                real(block)
            finally:
                self.processed += 1

        self.engine.process_one = counted

    def feed(self, blocks):
        for _ in range(blocks):
            target = self.processed + 1
            self.streams[0].input_queue.put(SILENCE)
            deadline = time.monotonic() + 5
            while self.processed < target and time.monotonic() < deadline:
                time.sleep(0.001)
            assert self.processed >= target, "the worker did not take the block"


@pytest.fixture
def worker():
    made = []

    def build(**kwargs):
        made.append(Worker(**kwargs))
        made[-1].engine.start()
        return made[-1]

    yield build
    for item in made:
        item.engine.stop()


def long_clip(engine, blocks=400):
    engine.processor.play(np.full(BLOCK * blocks, 0.1, dtype=np.float32))


def test_a_tap_that_raises_does_not_stop_ptt_from_following_the_clip(worker):
    """The reviewer's repro: a 3-block clip, a tap that raises, then 10 more blocks."""

    def broken_tap(received, transmitted):
        raise RuntimeError("tap bug")

    w = worker(on_audio=broken_tap)
    w.engine.processor.play(np.full(BLOCK * 3, 0.1, dtype=np.float32))
    w.feed(10)
    assert w.ptt == [True, False]


def test_a_broken_tap_still_lets_the_transmit_cap_fire(worker, monkeypatch):
    now = {"t": 1000.0}
    monkeypatch.setattr(engine_module, "_monotonic", lambda: now["t"])

    def broken_tap(received, transmitted):
        raise RuntimeError("tap bug")

    w = worker(on_audio=broken_tap, max_transmit_seconds=10.0)
    long_clip(w.engine)
    w.feed(2)
    now["t"] += 11
    w.feed(2)
    assert w.ptt == [True, False] and w.engine.tx_capped


def test_a_tap_failure_is_logged_once_and_not_per_block(worker, structured_events):
    def broken_tap(received, transmitted):
        raise RuntimeError("secret-detail")

    w = worker(on_audio=broken_tap)
    w.feed(20)
    failed = structured_events.named("audio.tap_failed")
    assert len(failed) == 1
    assert failed[0].error_code == "audio.tap" and "secret-detail" not in failed[0].model_dump_json()
    assert not structured_events.named("audio.block_failed")  # the block itself was fine


def break_processor(engine, failing):
    """`processor.process` raises while `failing()` is true, otherwise does its normal work."""
    real = engine.processor.process

    def process(chunk):
        if failing():
            raise RuntimeError("processor bug")
        return real(chunk)

    engine.processor.process = process


def test_a_processor_that_keeps_raising_while_keyed_is_unkeyed_by_the_engine(worker, structured_events):
    state = {"fail": False}
    w = worker()
    break_processor(w.engine, lambda: state["fail"])
    long_clip(w.engine)
    w.feed(3)
    assert w.ptt == [True]
    state["fail"] = True
    w.feed(KEYED_FAILURE_LIMIT + 5)
    assert w.ptt == [True, False]
    assert w.engine.transmitting is False
    released = structured_events.named("ptt.released_after_failures")
    assert len(released) == 1  # logged once, not on every failing block
    assert released[0].severity.name == "CRITICAL" and released[0].error_code == "audio.process"
    assert released[0].context == {"blocks": KEYED_FAILURE_LIMIT}


def test_one_bad_block_does_not_drop_a_transmission(worker):
    state = {"fail": False}
    w = worker()
    break_processor(w.engine, lambda: state["fail"])
    long_clip(w.engine)
    w.feed(3)
    state["fail"] = True
    w.feed(KEYED_FAILURE_LIMIT - 1)
    state["fail"] = False
    w.feed(5)
    assert w.ptt == [True]  # never unkeyed


def test_keying_resumes_when_the_processor_recovers(worker):
    state = {"fail": False}
    w = worker()
    break_processor(w.engine, lambda: state["fail"])
    long_clip(w.engine)
    w.feed(3)
    state["fail"] = True
    w.feed(KEYED_FAILURE_LIMIT + 2)
    assert w.ptt == [True, False]
    state["fail"] = False  # the clip never advanced while it failed, so the controller still wants to transmit
    w.feed(3)
    assert w.ptt == [True, False, True]


def test_the_transmit_cap_still_runs_when_the_processor_raises(worker, monkeypatch):
    """The cap is the independent backstop, so its clock must advance on blocks that fail too."""
    now = {"t": 1000.0}
    monkeypatch.setattr(engine_module, "_monotonic", lambda: now["t"])
    monkeypatch.setattr(engine_module, "KEYED_FAILURE_LIMIT", 10_000)  # leave the cap as the only thing that can unkey
    state = {"fail": False}
    w = worker(max_transmit_seconds=10.0)
    break_processor(w.engine, lambda: state["fail"])
    long_clip(w.engine)
    w.feed(2)
    state["fail"] = True
    w.feed(1)
    assert w.ptt == [True]
    now["t"] += 11
    w.feed(2)
    assert w.ptt == [True, False]
    assert w.engine.tx_capped and w.engine.tx_cap_trips == 1


def test_a_processor_that_raises_while_idle_does_not_touch_ptt(worker):
    state = {"fail": True}
    w = worker()
    break_processor(w.engine, lambda: state["fail"])
    w.feed(KEYED_FAILURE_LIMIT * 3)
    assert w.ptt == []
