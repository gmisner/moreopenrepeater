"""Background loops that carry timers, IDs and the lost-unkey guard must survive
an unexpected exception in one pass (issue #245, inventory finding F11).

Before this, an exception that escaped a `while True` loop ended its task for
good: the controller tick stopped (no time-out, ID or hang time), and the
AllStar and DMR senders stopped checking for a lost unkey. Each test makes the
loop raise once and checks it carries on. Simulation only.
"""
import asyncio
import logging

import pytest

from api import allstar_audio, dmr_audio as dmr_audio_mod, link_radio as link_radio_mod
from api.loops import run_announcement_loop, run_long_id_warm_loop, run_tick_loop
from api.supervise import LoopGuard

# Reuse the existing fixtures for the audio senders and the link radio.
# (pytest puts this directory on sys.path, so a plain import finds the sibling test modules.)
import test_allstar_audio as allstar_tests
import test_dmr_audio as dmr_tests
import test_link_radio as link_tests


async def run_for(coro, seconds):
    task = asyncio.create_task(coro)
    await asyncio.sleep(seconds)
    assert not task.done(), "the loop ended"
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


class Flaky:
    """A callable that raises on the first `fail` calls, then returns `result`."""

    def __init__(self, fail=1, result=None, error=RuntimeError("boom")):
        self.fail, self.result, self.error, self.calls = fail, result, error, 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        if self.calls <= self.fail:
            raise self.error
        return self.result


# --- LoopGuard ----------------------------------------------------------------

def test_guard_logs_the_first_failure_then_at_most_once_per_interval(caplog):
    now = [0.0]
    guard = LoopGuard("thing", logging.getLogger("t"), clock=lambda: now[0], log_every=10.0)
    with caplog.at_level(logging.WARNING, logger="t"):
        for _ in range(50):  # a fault every 50 ms for 2.5 s
            guard.failed(RuntimeError("x"))
            now[0] += 0.05
        assert [r.levelno for r in caplog.records] == [logging.ERROR]
        now[0] += 10
        guard.failed(RuntimeError("x"))
    assert len(caplog.records) == 2
    assert "51 in a row" in caplog.records[1].getMessage()
    assert guard.failures == 51 and guard.failing


def test_guard_reports_recovery_once(caplog):
    guard = LoopGuard("thing", logging.getLogger("t"), clock=lambda: 0.0)
    guard.failed(RuntimeError("x"))
    with caplog.at_level(logging.WARNING, logger="t"):
        guard.ok()
        guard.ok()
    recovered = [r for r in caplog.records if "recovered" in r.getMessage()]
    assert len(recovered) == 1 and not guard.failing


# --- controller tick ----------------------------------------------------------

class TickService:
    def __init__(self, fail):
        self.tick = Flaky(fail=fail)


def test_tick_loop_keeps_ticking_after_one_exception():
    async def scenario():
        service, last = TickService(fail=1), [0.0]
        await run_for(run_tick_loop(service, last, 0.005), 0.15)
        assert service.tick.calls > 3
        assert last[0] > 0  # progress is marked again once ticks succeed

    asyncio.run(scenario())


def test_a_tick_that_keeps_failing_does_not_look_like_progress():
    """So the watchdog still sees the stall, releases PTT and restarts the unit."""
    async def scenario():
        service, last = TickService(fail=10**9), [0.0]
        await run_for(run_tick_loop(service, last, 0.005), 0.1)
        started = last[0]
        assert service.tick.calls > 3
        assert last[0] == started  # only the initial mark, never refreshed

    asyncio.run(scenario())


# --- announcements and the long ID --------------------------------------------

class Announcements:
    def __init__(self, due):
        self.due, self.calls = due, 0

    def due_announcements(self):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("boom")
        return self.due if self.calls == 2 else []


def test_announcement_loop_survives_the_scheduler_raising():
    async def scenario():
        played = []

        async def render_and_queue(clip, on_linked_radio=False):
            played.append(clip)

        await run_for(
            run_announcement_loop(Announcements([("net-call", False)]), render_and_queue, 0.005, (KeyError,), logging.getLogger("t")),
            0.15,
        )
        assert played == ["net-call"]  # the pass after the failure still played it

    asyncio.run(scenario())


def test_announcement_loop_skips_only_the_announcement_that_fails():
    async def scenario():
        played = []

        async def render_and_queue(clip, on_linked_radio=False):
            if clip == "bad":
                raise KeyError(clip)
            played.append(clip)

        service = Announcements([("bad", False), ("good", False)])
        service.calls = 1  # skip the scheduler failure; this test is about one clip
        await run_for(
            run_announcement_loop(service, render_and_queue, 0.005, (KeyError,), logging.getLogger("t")), 0.1
        )
        assert played == ["good"]

    asyncio.run(scenario())


def test_long_id_warm_loop_survives_a_failure():
    async def scenario():
        warm = Flaky(fail=1)
        await run_for(run_long_id_warm_loop(warm, lambda: "config", 0.005, logging.getLogger("t")), 0.15)
        assert warm.calls > 2

    asyncio.run(scenario())


# --- link radio ---------------------------------------------------------------

def test_link_radio_pump_loop_survives_an_exception(monkeypatch):
    monkeypatch.setattr(link_radio_mod, "BLOCK_SECONDS", 0.005)
    rig = link_tests.Rig()
    flaky = Flaky(fail=1)
    monkeypatch.setattr(rig.link, "pump", flaky)

    async def scenario():
        await run_for(rig.link.run(), 0.15)

    asyncio.run(scenario())
    assert flaky.calls > 3


# --- AllStar and DMR senders: the lost-unkey guard must keep running ----------

def test_allstar_sender_still_unkeys_a_lost_node_when_sending_raises(monkeypatch):
    monkeypatch.setattr(allstar_audio, "UNKEY_TIMEOUT", 0.1)

    async def scenario():
        service, audio, link, node = await allstar_tests.start()
        link._link.take_radio = Flaky(fail=10**9)  # every send pass raises
        node.send(allstar_tests.voice(1), link.listen_port)
        await allstar_tests.wait_until(lambda: link.keyed)
        await allstar_tests.wait_until(lambda: not link.keyed)  # the guard still ran
        assert not link._task.done()
        await link.stop()
        node.transport.close()

    asyncio.run(scenario())


def test_dmr_sender_still_unkeys_a_lost_peer_when_sending_raises(monkeypatch):
    monkeypatch.setattr(dmr_audio_mod, "UNKEY_TIMEOUT", 0.1)

    async def scenario():
        service, audio, link, peer = await dmr_tests.start()
        link._link.take_radio = Flaky(fail=10**9)
        peer.send(dmr_tests.voice(1), link.listen_port)
        await dmr_tests.wait_until(lambda: link.keyed)
        await dmr_tests.wait_until(lambda: not link.keyed)
        assert not link._task.done()
        await link.stop()
        peer.transport.close()

    asyncio.run(scenario())
