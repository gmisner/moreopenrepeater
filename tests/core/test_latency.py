"""The core's opt-in latency diagnostics (#229): what held the audio thread up, in the core's own log."""
import asyncio
import gc
import logging
import time

import pytest

from core.latency import LatencyWatch, enabled_from_env


@pytest.fixture
def lines(caplog):
    caplog.set_level(logging.INFO, logger="moreopenrepeater.latency")
    return caplog


def test_it_is_off_unless_asked_for():
    assert enabled_from_env({}) is False
    assert enabled_from_env({"MOREOPENREPEATER_DIAG_LATENCY": "0"}) is False
    assert enabled_from_env({"MOREOPENREPEATER_DIAG_LATENCY": "1"}) is True


def test_a_slow_garbage_collection_is_reported_with_its_generation(lines):
    watch = LatencyWatch(gc_ms=1.0, stall_ms=10_000, lag_ms=10_000)
    watch.start()
    try:
        junk = [[i] for i in range(200_000)]  # enough live objects that a full collection takes a few ms
        gc.collect()
        assert junk
    finally:
        watch.stop()
    assert any("garbage collection" in r.message and "generation 2" in r.message for r in lines.records)


def test_a_quick_garbage_collection_is_not_reported(lines):
    watch = LatencyWatch(gc_ms=10_000, stall_ms=10_000, lag_ms=10_000)
    watch.start()
    try:
        gc.collect()
    finally:
        watch.stop()
    assert not [r for r in lines.records if "garbage collection" in r.message]


def test_a_thread_held_for_a_long_time_is_reported(lines):
    watch = LatencyWatch(gc_ms=10_000, stall_ms=30, lag_ms=10_000)
    watch.start()
    try:
        import random

        data = [random.random() for _ in range(600_000)]
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not [r for r in lines.records if "was held for" in r.message]:
            sorted(data)  # a C call that does not let go of the interpreter lock: no other thread runs meanwhile
    finally:
        watch.stop()
    assert any("was held for" in r.message for r in lines.records)


def test_a_late_event_loop_is_reported(lines):
    async def go():
        watch = LatencyWatch(gc_ms=10_000, stall_ms=10_000, lag_ms=30)
        watch.start(asyncio.get_running_loop())
        try:
            await asyncio.sleep(0.2)
            time.sleep(0.15)  # a handler that blocks the loop
            await asyncio.sleep(0.3)
        finally:
            watch.stop()

    asyncio.run(go())
    assert any("event loop" in r.message for r in lines.records)


def test_stopping_removes_its_hooks_and_ends_its_thread(lines):
    before = list(gc.callbacks)
    watch = LatencyWatch()
    watch.start()
    assert len(gc.callbacks) == len(before) + 1
    watch.stop()
    assert gc.callbacks == before and not watch.running


def test_a_reading_that_is_normal_logs_nothing(lines):
    watch = LatencyWatch(gc_ms=10_000, stall_ms=10_000, lag_ms=10_000)
    watch.start()
    time.sleep(0.2)
    watch.stop()
    quiet = [r for r in lines.records if r.name == "moreopenrepeater.latency" and "diagnostics on" not in r.message]
    assert quiet == []  # only the line that says it started
