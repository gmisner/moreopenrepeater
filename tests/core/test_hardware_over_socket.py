"""The core's hardware status over the control socket (Stage 3, #229)."""
import asyncio

import pytest
from control_helpers import get, make_server

from api import remote_core as remote_module
from api.remote_core import RemoteCore
from api.service.core_handle import CoreUnavailable
from contracts import CoreHardware
from core import control as control_module


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(remote_module, "RETRY_DELAYS", (0.05, 0.1))
    monkeypatch.setattr(remote_module, "HARDWARE_POLL_SECONDS", 0.05)


async def until(condition, timeout=5.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not condition():
        assert asyncio.get_running_loop().time() < deadline, "timed out"
        await asyncio.sleep(0.02)


def run(core, sock_dir, scenario):
    async def go():
        path = sock_dir / "core.sock"
        server = make_server(core, path)
        await server.start()
        try:
            return await scenario(path, server)
        finally:
            await server.stop()

    return asyncio.run(go())


def test_every_part_reports_its_own_status(core, sock_dir):
    async def scenario(path, server):
        return await get(path, "/hardware")

    status, body = run(core, sock_dir, scenario)
    hardware = CoreHardware.model_validate(body)
    assert status == 200 and hardware.unavailable == []
    assert set(hardware.statuses) == set(control_module.HARDWARE_PARTS)
    assert hardware.statuses["gpio"] == core.runtime.gpio.status()
    assert hardware.statuses["live_audio"]["enabled"] == core.runtime.live_audio.status()["enabled"]
    assert set(hardware.statuses["audio_device_list"]) == {"devices", "error"}  # the sound cards, for an API with no audio libraries


def test_a_part_that_fails_or_is_slow_is_named_and_the_rest_still_answer(core, sock_dir, monkeypatch):
    monkeypatch.setattr(control_module, "HARDWARE_DEADLINE_SECONDS", 0.2)

    def boom():
        raise RuntimeError("device vanished")

    async def slow():
        await asyncio.sleep(5)

    monkeypatch.setattr(core.runtime.gpio, "status", boom)
    monkeypatch.setattr(core.runtime.dmr, "status", slow)

    async def scenario(path, server):
        return await get(path, "/hardware")

    _, body = run(core, sock_dir, scenario)
    assert sorted(body["unavailable"]) == ["dmr", "gpio"]
    assert "link_radio" in body["statuses"] and "gpio" not in body["statuses"]


def test_a_status_with_odd_values_still_becomes_plain_json(core, sock_dir, monkeypatch):
    import datetime
    import pathlib

    monkeypatch.setattr(core.runtime.tx_fan, "status", lambda: {"when": datetime.datetime(2026, 1, 1), "where": pathlib.Path("/x")})

    async def scenario(path, server):
        return await get(path, "/hardware")

    _, body = run(core, sock_dir, scenario)
    assert body["statuses"]["tx_fan"] == {"when": "2026-01-01 00:00:00", "where": "/x"}


def test_only_get_is_allowed(core, sock_dir):
    from control_helpers import parse, raw

    async def scenario(path, server):
        return parse(await raw(path, b"POST /hardware HTTP/1.1\r\nHost: core\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n{}"))[0]

    assert run(core, sock_dir, scenario) == 405


# -- RemoteCore -------------------------------------------------------------------------------------


def test_the_remote_core_serves_what_the_core_last_reported(core, sock_dir):
    async def scenario(path, server):
        remote = RemoteCore(path)
        remote.start()
        try:
            await until(lambda: remote._hardware_at is not None)
            assert remote.hardware_status("gpio") == core.runtime.gpio.status()
            with pytest.raises(CoreUnavailable):
                remote.hardware_status("nonesuch")  # a part the core did not report
        finally:
            await remote.stop()

    run(core, sock_dir, scenario)


def test_it_refuses_to_answer_before_anything_is_read_and_after_the_core_goes_quiet(core, sock_dir):
    with pytest.raises(CoreUnavailable):
        RemoteCore(sock_dir / "x.sock").hardware_status("gpio")

    now = {"t": 100.0}

    async def scenario(path, server):
        remote = RemoteCore(path, clock=lambda: now["t"])
        remote.start()
        try:
            await until(lambda: remote._hardware_at is not None)
            remote.hardware_status("gpio")
            now["t"] += remote_module.HARDWARE_STALE_SECONDS + 1  # time passes with nothing new
            remote._last_event_at = now["t"]  # (the core is still heard from, just not read)
            remote._hardware_at = 100.0
            with pytest.raises(CoreUnavailable):
                remote.hardware_status("gpio")
        finally:
            await remote.stop()

    run(core, sock_dir, scenario)


def test_a_change_in_the_core_shows_up_at_the_next_read(core, sock_dir, monkeypatch):
    async def scenario(path, server):
        remote = RemoteCore(path)
        remote.start()
        try:
            await until(lambda: remote._hardware_at is not None)
            monkeypatch.setattr(core.runtime.tx_fan, "status", lambda: {"on": True, "reason": "hot"})
            await until(lambda: remote.hardware_status("tx_fan").get("reason") == "hot")
        finally:
            await remote.stop()

    run(core, sock_dir, scenario)
