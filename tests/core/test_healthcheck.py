"""The container health check against a real control socket (Stage 3, #229)."""
import asyncio
import time

from control_helpers import make_server

from core import healthcheck


def test_a_running_core_is_healthy(core, sock_dir):
    path = sock_dir / "core.sock"
    core.runtime.last_tick[0] = time.monotonic()  # the tick has run

    async def go():
        server = make_server(core, path)
        await server.start()
        try:
            # The check blocks on a socket, so it runs off the loop the server is on.
            return await asyncio.get_running_loop().run_in_executor(None, healthcheck.check, path)
        finally:
            await server.stop()

    healthy, reason = asyncio.run(go())
    assert healthy, reason
    assert "core health is" in reason


def test_a_core_that_is_not_there_is_unhealthy(sock_dir):
    healthy, reason = healthcheck.check(sock_dir / "nothing.sock")
    assert not healthy and "cannot reach" in reason


def test_a_failed_core_is_unhealthy(core, sock_dir):
    path = sock_dir / "core.sock"
    core.runtime.last_tick[0] = 0.0  # no progress for far longer than the stall limit

    async def go():
        server = make_server(core, path, clock=lambda: 10_000.0)
        await server.start()
        try:
            return await asyncio.get_running_loop().run_in_executor(None, healthcheck.check, path)
        finally:
            await server.stop()

    healthy, reason = asyncio.run(go())
    assert not healthy and "failed" in reason


def test_main_needs_the_socket_variable(capsys):
    assert healthcheck.main({}) == 1
    assert "MOREOPENREPEATER_CORE_SOCKET" in capsys.readouterr().err
