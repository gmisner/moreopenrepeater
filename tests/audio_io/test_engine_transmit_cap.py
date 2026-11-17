"""The engine ends a transmission that outlasts its limit, on its own thread, even
if the controller never drops PTT (issue #246, inventory finding F5).

Time is a fake clock and audio is synthetic. Simulation only: nothing here
shows what a real transmitter does.
"""
import numpy as np
import pytest

from audio_io import engine as engine_module
from test_engine import BLOCK, make_engine

SILENCE = np.zeros(BLOCK, dtype=np.float32)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock(monkeypatch):
    fake = Clock()
    monkeypatch.setattr(engine_module, "_monotonic", fake)
    return fake


def hold_ptt(engine, clock, seconds, step=1.0):
    """The controller keeps PTT asserted and blocks keep arriving."""
    engine.processor.set_ptt(True)
    elapsed = 0.0
    while elapsed <= seconds:
        engine.process_one(SILENCE)
        clock.now += step
        elapsed += step


def test_ptt_held_past_the_limit_is_released_by_the_engine(clock):
    engine, _, ptt, _ = make_engine(max_transmit_seconds=60.0)
    hold_ptt(engine, clock, 61)
    assert ptt == [True, False]
    assert engine.tx_capped and engine.tx_cap_trips == 1
    assert engine.transmitting is False


def test_a_transmission_under_the_limit_is_left_alone(clock):
    engine, _, ptt, _ = make_engine(max_transmit_seconds=60.0)
    hold_ptt(engine, clock, 59)
    assert ptt == [True]
    assert not engine.tx_capped and engine.tx_cap_trips == 0


def test_no_limit_means_no_cap(clock):
    engine, _, ptt, _ = make_engine(max_transmit_seconds=None)
    hold_ptt(engine, clock, 100_000, step=5000.0)
    assert ptt == [True] and not engine.tx_capped


def test_output_is_silenced_while_capped_so_a_vox_radio_stops_too(clock):
    engine, _, _, _ = make_engine(max_transmit_seconds=10.0)
    engine.processor.play(np.full(BLOCK * 400, 0.3, dtype=np.float32))  # a long clip keeps it keyed

    def drain():
        blocks = []
        while not engine._output.empty():
            blocks.append(engine._output.get_nowait())
        return np.concatenate(blocks) if blocks else np.zeros(0)

    engine.process_one(SILENCE)
    clock.now += 5
    engine.process_one(SILENCE)
    assert np.abs(drain()).max() > 0.1  # audio goes out before the cap
    clock.now += 10
    engine.process_one(SILENCE)  # trips here
    drain()
    clock.now += 1
    engine.process_one(SILENCE)
    assert engine.tx_capped
    assert np.abs(drain()).max() == 0.0  # silent after


def test_keying_is_allowed_again_once_the_source_stops(clock):
    engine, _, ptt, _ = make_engine(max_transmit_seconds=10.0)
    hold_ptt(engine, clock, 11)
    assert engine.tx_capped
    engine.processor.set_ptt(False)
    engine.process_one(SILENCE)
    assert not engine.tx_capped
    engine.processor.set_ptt(True)
    engine.process_one(SILENCE)
    assert ptt == [True, False, True]


def test_the_cap_does_not_trip_again_on_every_block(clock):
    engine, _, ptt, _ = make_engine(max_transmit_seconds=10.0)
    hold_ptt(engine, clock, 40)
    assert engine.tx_cap_trips == 1
    assert ptt == [True, False]


def test_the_callback_runs_once_and_a_failing_callback_does_not_stop_the_cap(clock):
    seen = []

    def boom(seconds):
        seen.append(seconds)
        raise RuntimeError("handler bug")

    engine, _, ptt, _ = make_engine(max_transmit_seconds=10.0, on_transmit_capped=boom)
    hold_ptt(engine, clock, 20)
    assert len(seen) == 1 and seen[0] > 10
    assert ptt == [True, False]


def test_the_limit_can_change_while_running(clock):
    engine, _, ptt, _ = make_engine(max_transmit_seconds=1000.0)
    hold_ptt(engine, clock, 30)
    assert ptt == [True]
    engine.set_max_transmit_seconds(20.0)
    engine.process_one(SILENCE)
    assert ptt == [True, False]


def test_time_when_keying_is_not_allowed_does_not_count(clock):
    allowed = {"ok": False}
    engine, _, ptt, _ = make_engine(max_transmit_seconds=10.0, ptt_allowed=lambda: allowed["ok"])
    hold_ptt(engine, clock, 30)  # the controller wants PTT but it is not allowed
    assert ptt == [] and not engine.tx_capped
    allowed["ok"] = True
    engine.process_one(SILENCE)
    assert ptt == [True] and not engine.tx_capped
