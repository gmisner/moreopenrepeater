"""The runtime's start-up and shutdown order must not change (Stage 3 slice 5, #229).

Audio and PTT bring-up, READY=1, the GPIO and the shutdown sequence were
written as one lifespan and are now split between `CoreRuntime` and the web
app's runtime. This pins the order of the calls that matter to the radio, so a
refactor that moves code cannot quietly reorder them. It records calls on a
real runtime with in-memory stores and no devices. Simulation only.
"""
import asyncio

import pytest
from fastapi import FastAPI

from api import runtime as runtime_module
from api.runtime import RuntimeBuilder

EXPECTED = [
    "fan.start",
    "live_audio.attach",
    "monitor_receiver.attach",
    "link_radio.attach",
    "link_radio.run",
    "autopatch.start",
    "allstar_node.start",
    "sd_notify READY=1",
    "gpio.start",
    "remote_base.attach",
    "-- serving --",
    "sd_notify STOPPING=1",
    "gpio.stop",
    "fan.stop",
    "allstar_node.stop",
    "dmr_audio.stop",
    "autopatch.stop",
    "live_audio.shutdown",
    "monitor_receiver.shutdown",
    "link_radio.shutdown",
    "activity_store.flush",
    "site_history.flush",
]


def record(events, name, target, attr, is_async=False):
    original = getattr(target, attr)

    if is_async:
        async def wrapper(*args, **kwargs):
            events.append(name)
            return await original(*args, **kwargs)
    else:
        def wrapper(*args, **kwargs):
            events.append(name)
            return original(*args, **kwargs)

    setattr(target, attr, wrapper)


@pytest.fixture
def events(monkeypatch):
    recorded = []

    def fake_notify(message, env=None):
        recorded.append(f"sd_notify {message}")
        return True

    # Whichever module sends it (the runtime before the split, the core runtime after).
    monkeypatch.setattr(runtime_module, "sd_notify", fake_notify, raising=False)
    # Never start a real text-to-speech server from a test (a developer machine may have one).
    monkeypatch.setattr(runtime_module, "_kokoro_service_for", lambda renderer: None, raising=False)
    monkeypatch.setattr(runtime_module, "_piper_service_for", lambda renderer, voice="": None, raising=False)
    try:
        from api import core_runtime

        monkeypatch.setattr(core_runtime, "sd_notify", fake_notify, raising=False)
        monkeypatch.setattr(core_runtime, "kokoro_service_for", lambda renderer: None)
        monkeypatch.setattr(core_runtime, "piper_service_for", lambda renderer, voice="": None)
    except ImportError:
        pass
    return recorded


def test_start_up_and_shutdown_order_is_unchanged(events):
    runtime = RuntimeBuilder(start_background_tick=True).build()
    record(events, "fan.start", runtime.tx_fan, "start")
    record(events, "live_audio.attach", runtime.live_audio, "attach")
    record(events, "monitor_receiver.attach", runtime.monitor_receiver, "attach")
    record(events, "link_radio.attach", runtime.link_radio, "attach")
    record(events, "link_radio.run", runtime.link_radio, "run")  # called, then wrapped in a task
    record(events, "autopatch.start", runtime.autopatch, "start", is_async=True)
    record(events, "allstar_node.start", runtime.allstar_node, "start", is_async=True)
    record(events, "gpio.start", runtime.gpio, "start")
    record(events, "remote_base.attach", runtime.remote_base, "attach")
    record(events, "gpio.stop", runtime.gpio, "stop", is_async=True)
    record(events, "fan.stop", runtime.tx_fan, "stop", is_async=True)
    record(events, "allstar_node.stop", runtime.allstar_node, "stop", is_async=True)
    record(events, "dmr_audio.stop", runtime.dmr_audio, "stop", is_async=True)
    record(events, "autopatch.stop", runtime.autopatch, "stop", is_async=True)
    record(events, "live_audio.shutdown", runtime.live_audio, "shutdown")
    record(events, "monitor_receiver.shutdown", runtime.monitor_receiver, "shutdown")
    record(events, "link_radio.shutdown", runtime.link_radio, "shutdown")
    record(events, "activity_store.flush", runtime.activity_store, "flush")
    record(events, "site_history.flush", runtime.site_sensors.history, "flush")

    runtime.attach_agent(FastAPI())

    async def scenario():
        async with runtime.lifespan(None):
            events.append("-- serving --")
            await asyncio.sleep(0.05)

    asyncio.run(scenario())
    assert events == EXPECTED
