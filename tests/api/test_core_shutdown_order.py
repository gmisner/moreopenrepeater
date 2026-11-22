"""Shutdown releases PTT first, on every way out, and one stuck integration cannot block the rest (review finding).

Fakes only: these show the order and the timeouts. They do not show what a real keyed transmitter or a real
container stop does.
"""
import asyncio
from types import SimpleNamespace

import pytest

from api import core_runtime as core_runtime_module
from api.core_runtime import CoreRuntime
from core.main import serve


class Recorder:
    def __init__(self):
        self.log = []


def build(rec, *, hang=(), fail=(), ptt_fail=()):
    """A CoreRuntime made of fakes that write to one shared log."""

    def sync(name, raises=False):
        def call():
            rec.log.append(name)
            if raises:
                raise RuntimeError(f"{name} bug")

        return call

    def stoppable(name):
        async def stop():
            rec.log.append(f"{name}.stop")
            if name in hang:
                await asyncio.sleep(3600)
            if name in fail:
                raise RuntimeError(f"{name} bug")

        return SimpleNamespace(stop=stop)

    watchdog = SimpleNamespace(watch=lambda *a, **k: None, stop=lambda: None)
    return CoreRuntime(
        service=None, renderer=SimpleNamespace(),
        live_audio=SimpleNamespace(
            release_ptt=sync("live.release_ptt", "live" in ptt_fail), progress=lambda: None, shutdown=sync("live.shutdown")
        ),
        monitor_receiver=SimpleNamespace(shutdown=sync("monitor.shutdown")),
        link_radio=SimpleNamespace(
            release_ptt=sync("link.release_ptt", "link" in ptt_fail), progress=lambda: None, shutdown=sync("link.shutdown")
        ),
        remote_base=None, gpio=stoppable("gpio"), tx_fan=stoppable("tx_fan"), autopatch=stoppable("autopatch"),
        allstar_node=stoppable("allstar"), dmr=None, dmr_audio=stoppable("dmr"), watchdog=watchdog,
    )


@pytest.fixture(autouse=True)
def quick_timeout(monkeypatch):
    monkeypatch.setattr(core_runtime_module, "INTEGRATION_STOP_SECONDS", 0.05)


def test_ptt_is_released_before_any_integration_stops():
    rec = Recorder()
    asyncio.run(build(rec).stop_devices())
    assert rec.log[:2] == ["live.release_ptt", "link.release_ptt"]
    assert rec.log[2:7] == ["gpio.stop", "tx_fan.stop", "allstar.stop", "dmr.stop", "autopatch.stop"]
    assert rec.log[7:] == ["live.shutdown", "monitor.shutdown", "link.shutdown"]


def test_an_integration_that_hangs_is_given_up_on_and_the_rest_still_stop(structured_events):
    rec = Recorder()
    asyncio.run(asyncio.wait_for(build(rec, hang={"allstar"}).stop_devices(), 5))
    assert rec.log.index("live.release_ptt") < rec.log.index("allstar.stop")
    assert rec.log[rec.log.index("allstar.stop") + 1 :] == [
        "dmr.stop", "autopatch.stop", "live.shutdown", "monitor.shutdown", "link.shutdown",
    ]
    failed = structured_events.named("core.shutdown_step_failed")
    assert [(e.context["target"], e.context["reason"]) for e in failed] == [("allstar", "timed_out")]


def test_an_integration_that_raises_is_logged_and_the_rest_still_stop(structured_events):
    rec = Recorder()
    asyncio.run(build(rec, fail={"gpio", "autopatch"}).stop_devices())
    assert rec.log.index("live.release_ptt") < rec.log.index("gpio.stop")
    assert "live.shutdown" in rec.log and "link.shutdown" in rec.log
    failed = structured_events.named("core.shutdown_step_failed")
    assert [e.context["target"] for e in failed] == ["gpio", "autopatch"]
    assert all(e.error_code == "core.shutdown" and e.context["exception"] == "RuntimeError" for e in failed)
    assert "gpio bug" not in "".join(e.model_dump_json() for e in failed)


def test_the_link_radio_is_unkeyed_even_if_the_main_release_raises(structured_events):
    rec = Recorder()
    asyncio.run(build(rec, ptt_fail={"live"}).stop_devices())
    assert rec.log[:2] == ["live.release_ptt", "link.release_ptt"]
    assert "autopatch.stop" in rec.log
    assert [e.context["target"] for e in structured_events.named("core.shutdown_step_failed")] == ["ptt_live"]


class FakeApp:
    def __init__(self, runtime):
        self.runtime = runtime
        self.activity_store = SimpleNamespace(flush=lambda: runtime.rec.log.append("activity.flush"))
        self.state_path = None


def fake_core(rec, *, start_error=None, hang=()):
    """The real stop path, with the start-up steps replaced by fakes."""
    core = build(rec, hang=hang)
    core.rec = rec
    core.watchdog = SimpleNamespace(stop=lambda: rec.log.append("watchdog.stop"))

    def step(name):
        return lambda *a, **k: rec.log.append(name)

    async def start_tts():
        rec.log.append("start_tts")

    async def start_audio_and_links(loop, tasks):
        rec.log.append("start_audio")
        if start_error:
            raise start_error

    async def stop_tts():
        rec.log.append("stop_tts")

    core.start_fan = step("start_fan")
    core.start_tick = step("start_tick")
    core.start_announcements = step("start_announcements")
    core.start_tts = start_tts
    core.warm_renderer = step("warm_renderer")
    core.start_long_id = step("start_long_id")
    core.start_audio_and_links = start_audio_and_links
    core.notify_ready = step("notify_ready")
    core.start_io = step("start_io")
    core.announce_stopping = step("announce_stopping")
    core.stop_tts = stop_tts
    return core


def run_serve(core):
    async def go():
        stop = asyncio.Event()
        stop.set()
        await asyncio.wait_for(serve(FakeApp(core), stop), 10)

    asyncio.run(go())


def test_a_clean_stop_releases_ptt_before_anything_else_is_stopped():
    rec = Recorder()
    run_serve(fake_core(rec))
    after_start = rec.log[rec.log.index("start_io") + 1 :]
    assert after_start[:2] == ["live.release_ptt", "link.release_ptt"]
    assert after_start.index("announce_stopping") > 1
    assert "gpio.stop" in after_start and "stop_tts" in after_start


def test_an_exception_while_starting_still_releases_ptt_and_still_raises():
    rec = Recorder()
    with pytest.raises(RuntimeError, match="no sound card"):
        run_serve(fake_core(rec, start_error=RuntimeError("no sound card")))
    after = rec.log[rec.log.index("start_audio") + 1 :]
    assert after[:2] == ["live.release_ptt", "link.release_ptt"]
    assert "live.shutdown" in after  # the rest of shutdown ran too


def test_a_hung_integration_cannot_keep_serve_from_finishing():
    rec = Recorder()
    run_serve(fake_core(rec, hang={"autopatch"}))  # the 10 s outer timeout would raise if it blocked
    assert rec.log.index("live.release_ptt") < rec.log.index("autopatch.stop")
    assert "stop_tts" in rec.log
