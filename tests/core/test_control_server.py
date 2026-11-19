"""The core's control socket (Stage 3 slice 7a, #229): read-only health and status
over HTTP on a Unix socket. Simulation only: no devices, no Pi."""
import asyncio
import os
import socket
import stat
from pathlib import Path

import httpx
import pytest

from contracts import CoreStatus, HealthReport, HealthState
from core import control as control_module
from core.control import socket_path_from_env
from core.main import serve
from control_helpers import get, make_server, parse, raw


async def _accepts(path) -> bool:
    try:
        _, writer = await asyncio.open_unix_connection(str(path))
    except OSError:
        return False
    writer.close()
    return True


# --- answers ----------------------------------------------------------------------

def test_status_is_a_core_status_contract_and_claims_nothing_about_rf(core, sock_dir):
    async def scenario():
        server = make_server(core, sock_dir / "core.sock")
        assert await server.start()
        try:
            status, body = await get(sock_dir / "core.sock")
            assert status == 200
            message = CoreStatus.model_validate(body)
            assert message.header.source.module == "core"
            assert message.ptt_commanded is False
            assert message.ptt_observed is None and message.ptt_evidence.value == "none"
            core.service.simulate_cos(active=True)
            _, body = await get(sock_dir / "core.sock")
            assert CoreStatus.model_validate(body).state == "receiving"
            assert CoreStatus.model_validate(body).ptt_commanded is True  # the intent, not a measurement
        finally:
            await server.stop()

    asyncio.run(scenario())


def test_health_reports_a_fresh_tick_as_ok_and_a_stalled_or_missing_one_as_failed(core, sock_dir):
    async def scenario():
        clock = {"now": 1000.0}
        server = make_server(core, sock_dir / "core.sock", clock=lambda: clock["now"])
        await server.start()
        try:
            core.runtime.last_tick[0] = None  # the tick has not run yet
            _, body = await get(sock_dir / "core.sock", "/health")
            health = HealthReport.model_validate(body)
            assert health.state is HealthState.FAILED
            assert [c.name for c in health.checks] == ["controller.tick", "audio.engine", "link_radio.engine"]

            core.runtime.last_tick[0] = clock["now"] - 1
            health = HealthReport.model_validate((await get(sock_dir / "core.sock", "/health"))[1])
            assert health.state is HealthState.OK  # audio and link radio are off, which is fine

            core.runtime.last_tick[0] = clock["now"] - 60
            health = HealthReport.model_validate((await get(sock_dir / "core.sock", "/health"))[1])
            assert health.state is HealthState.FAILED
            assert health.checks[0].error.code == "progress.stalled"
        finally:
            await server.stop()

    asyncio.run(scenario())


def test_unknown_paths_and_other_methods_are_refused(core, sock_dir):
    async def scenario():
        server = make_server(core, sock_dir / "core.sock")
        await server.start()
        try:
            assert (await get(sock_dir / "core.sock", "/nope"))[0] == 404
            assert (await get(sock_dir / "core.sock", "/status?x=1"))[0] == 200
            post = b"POST /status HTTP/1.1\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n{}"
            assert parse(await raw(sock_dir / "core.sock", post))[0] == 405
            for method in ("PUT", "DELETE"):
                assert parse(await raw(sock_dir / "core.sock", f"{method} /status HTTP/1.1\r\n\r\n".encode()))[0] == 405
        finally:
            await server.stop()

    asyncio.run(scenario())


def test_the_intended_client_works(core, sock_dir):
    """The API will talk to this with httpx over the socket."""
    async def scenario():
        server = make_server(core, sock_dir / "core.sock")
        await server.start()
        try:
            transport = httpx.AsyncHTTPTransport(uds=str(sock_dir / "core.sock"))
            async with httpx.AsyncClient(transport=transport, base_url="http://core") as client:
                response = await client.get("/status")
                assert response.status_code == 200
                assert CoreStatus.model_validate(response.json()).header.schema_version == 1
        finally:
            await server.stop()

    asyncio.run(scenario())


# --- bad clients ----------------------------------------------------------------------

@pytest.mark.parametrize(
    "request_bytes,expected",
    [
        (b"garbage\r\n\r\n", 400),
        (b"GET /status\r\n\r\n", 400),
        (b"GET /status HTTP/1.1\r\nContent-Length: 5\r\n\r\nhello", 400),
        (b"GET /status HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n", 400),
        (b"GET /" + b"a" * 5000 + b" HTTP/1.1\r\n\r\n", 400),
        (b"GET /status HTTP/1.1\r\n" + b"X: " + b"y" * 9000 + b"\r\n\r\n", 431),
        (b"GET /status HTTP/1.1\r\n" + b"X: y\r\n" * 60 + b"\r\n", 431),
    ],
)
def test_malformed_and_oversized_requests_get_an_error_not_a_crash(core, sock_dir, request_bytes, expected):
    async def scenario():
        server = make_server(core, sock_dir / "core.sock")
        await server.start()
        try:
            assert parse(await raw(sock_dir / "core.sock", request_bytes))[0] == expected
            assert (await get(sock_dir / "core.sock"))[0] == 200  # still serving
        finally:
            await server.stop()

    asyncio.run(scenario())


def test_a_silent_client_times_out_and_does_not_hold_up_others(core, sock_dir, monkeypatch):
    monkeypatch.setattr(control_module, "REQUEST_DEADLINE_SECONDS", 0.3)

    async def scenario():
        server = make_server(core, sock_dir / "core.sock")
        await server.start()
        try:
            reader, writer = await asyncio.open_unix_connection(str(sock_dir / "core.sock"))  # says nothing
            assert (await get(sock_dir / "core.sock"))[0] == 200  # others are served meanwhile
            data = await asyncio.wait_for(reader.read(), 5)
            assert parse(data)[0] == 408
            writer.close()
        finally:
            await server.stop()

    asyncio.run(scenario())


def test_a_client_that_disconnects_mid_request_changes_nothing(core, sock_dir):
    async def scenario():
        server = make_server(core, sock_dir / "core.sock")
        await server.start()
        try:
            for _ in range(20):
                await raw(sock_dir / "core.sock", b"GET /sta", read=False)  # hangs up half way
                await raw(sock_dir / "core.sock", b"GET /status HTTP/1.1\r\n\r\n", read=False)  # hangs up before the answer
            assert (await get(sock_dir / "core.sock"))[0] == 200
        finally:
            await server.stop()

    asyncio.run(scenario())


def test_connections_beyond_the_limit_are_turned_away_and_service_recovers(core, sock_dir, monkeypatch):
    monkeypatch.setattr(control_module, "REQUEST_DEADLINE_SECONDS", 0.5)
    monkeypatch.setattr(control_module, "MAX_CONNECTIONS", 3)

    async def scenario():
        server = make_server(core, sock_dir / "core.sock")
        await server.start()
        try:
            held = [await asyncio.open_unix_connection(str(sock_dir / "core.sock")) for _ in range(3)]
            await asyncio.sleep(0.1)
            reader, writer = await asyncio.open_unix_connection(str(sock_dir / "core.sock"))
            assert await asyncio.wait_for(reader.read(), 5) == b""  # closed straight away
            assert server.refused >= 1
            writer.close()
            await asyncio.sleep(0.8)  # the held ones time out
            assert (await get(sock_dir / "core.sock"))[0] == 200
            for _, w in held:
                w.close()
        finally:
            await server.stop()

    asyncio.run(scenario())


# --- the socket itself ------------------------------------------------------------------

def test_the_socket_is_restricted_and_removed_on_stop(core, sock_dir):
    path = sock_dir / "run" / "core.sock"

    async def scenario():
        server = make_server(core, path)
        assert await server.start()
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o660
        assert stat.S_IMODE(os.stat(path.parent).st_mode) == 0o750
        await server.stop()
        assert not path.exists()

    asyncio.run(scenario())


def test_a_stale_socket_from_a_crash_is_replaced(core, sock_dir):
    path = sock_dir / "core.sock"
    leftover = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    leftover.bind(str(path))
    leftover.close()  # a crash: the file stays, nobody listens
    assert path.exists()

    async def scenario():
        server = make_server(core, path)
        assert await server.start()
        assert (await get(path))[0] == 200
        await server.stop()

    asyncio.run(scenario())


def test_a_file_that_is_not_a_socket_is_never_removed_and_the_core_carries_on(core, sock_dir):
    path = sock_dir / "core.sock"
    path.write_text("precious")

    async def scenario():
        server = make_server(core, path)
        assert await server.start() is False  # logs and carries on
        await server.stop()

    asyncio.run(scenario())
    assert path.read_text() == "precious"


def test_the_socket_path_comes_from_the_environment_and_is_off_by_default():
    assert socket_path_from_env({}) is None
    assert socket_path_from_env({"MOREOPENREPEATER_CORE_SOCKET": "  "}) is None
    assert socket_path_from_env({"MOREOPENREPEATER_CORE_SOCKET": "/run/x/core.sock"}) == Path("/run/x/core.sock")


# --- inside the running core -------------------------------------------------------------

def test_the_core_keeps_ticking_while_the_socket_is_hammered_and_removes_it_on_stop(core, sock_dir):
    path = sock_dir / "core.sock"

    async def scenario():
        stop = asyncio.Event()
        task = asyncio.create_task(serve(core, stop, control_socket=path))
        for _ in range(200):
            await asyncio.sleep(0.02)
            if path.exists() and core.runtime.last_tick[0] and await _accepts(path):
                break
        assert path.exists()
        before = core.runtime.last_tick[0]
        statuses = []
        for _ in range(15):  # 150 requests, ten at a time (the server serves at most 16 at once)
            statuses += await asyncio.gather(*(get(path, "/status" if i % 2 else "/health") for i in range(10)))
        assert all(code == 200 for code, _ in statuses)
        await asyncio.sleep(0.3)
        assert core.runtime.last_tick[0] > before  # the controller tick never stopped
        stop.set()
        await asyncio.wait_for(task, 10)
        assert not path.exists()

    asyncio.run(scenario())


def test_the_core_runs_without_the_socket_when_it_is_not_configured(core):
    async def scenario():
        stop = asyncio.Event()
        task = asyncio.create_task(serve(core, stop))
        await asyncio.sleep(0.3)
        assert core.runtime.last_tick[0]
        stop.set()
        await asyncio.wait_for(task, 10)

    asyncio.run(scenario())


def test_the_unit_sets_the_socket_inside_the_runtime_directory():
    unit = (Path(__file__).resolve().parents[2] / "packaging" / "moreopenrepeater-core.service").read_text()
    assert "Environment=MOREOPENREPEATER_CORE_SOCKET=/run/moreopenrepeater/core.sock" in unit
    assert "RuntimeDirectory=moreopenrepeater" in unit and "RuntimeDirectoryMode=0750" in unit


def test_health_uses_the_watchdogs_stall_limit_so_the_two_cannot_disagree(core, sock_dir):
    """`MOREOPENREPEATER_STALL_SECONDS` sets how long without progress is a stall. The container's health and the
    watchdog that restarts it have to agree, or one would say failed while the other waits."""
    async def scenario():
        clock = {"now": 1000.0}
        server = make_server(core, sock_dir / "core.sock", clock=lambda: clock["now"])
        await server.start()
        try:
            async def state_after(seconds_without_a_tick):
                core.runtime.last_tick[0] = clock["now"] - seconds_without_a_tick
                return HealthReport.model_validate((await get(sock_dir / "core.sock", "/health"))[1])

            assert core.runtime.watchdog.stall_seconds == 10.0
            assert (await state_after(8)).state is HealthState.OK
            assert (await state_after(12)).state is HealthState.FAILED

            core.runtime.watchdog._stall_seconds = 30.0  # the setting, as build_core would have read it
            assert (await state_after(12)).state is HealthState.OK  # a longer limit is waited out
            failed = await state_after(40)
            assert failed.state is HealthState.FAILED and failed.checks[0].error.message == "no progress for over 30 s"

            core.runtime.watchdog._stall_seconds = 3.0
            assert (await state_after(5)).state is HealthState.FAILED  # a shorter one trips sooner
        finally:
            await server.stop()

    asyncio.run(scenario())
