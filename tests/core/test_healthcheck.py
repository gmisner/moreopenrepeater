"""The container health check against a real control socket (Stage 3, #229)."""
import asyncio
import subprocess
import sys
import time
from pathlib import Path

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


def test_the_variable_name_matches_the_one_the_core_reads():
    from core.control import socket_path_from_env

    assert socket_path_from_env({healthcheck.SOCKET_VARIABLE: "/x/core.sock"}) == Path("/x/core.sock")


def test_it_imports_nothing_heavy():
    """On a Pi, importing asyncio, pydantic and the contracts cost over 5 s per run."""
    src = Path(__file__).resolve().parents[2] / "src"
    code = (
        f"import sys; sys.path.insert(0, {str(src)!r}); import core.healthcheck; "
        "heavy = {'asyncio', 'contracts', 'pydantic', 'numpy', 'core.control'} & set(sys.modules); "
        "print(sorted(heavy)); sys.exit(1 if heavy else 0)"
    )
    run = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True)
    assert run.returncode == 0, run.stdout + run.stderr
