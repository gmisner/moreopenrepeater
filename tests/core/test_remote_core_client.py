"""The API's client for the core's socket builds no TLS context (#229).

The control socket is plain HTTP over a Unix socket, so there is nothing to encrypt, but httpx builds
an SSL context for every client it creates unless told not to, which loads and parses the whole
certificate bundle. The API makes a client for each request, so on a Pi that was a sixth of its Python
time, spent on a poll that runs every second.
"""
import asyncio
import ssl

import pytest
from control_helpers import make_server

from api.remote_core import RemoteCore


@pytest.fixture
def no_tls(monkeypatch):
    """Any attempt to build a default TLS context (the expensive one) fails the test."""
    built = []

    def refuse(*args, **kwargs):
        built.append(1)
        raise AssertionError("a TLS context was built for the local socket")

    monkeypatch.setattr(ssl, "create_default_context", refuse)
    return built


def test_the_client_for_the_socket_builds_no_tls_context(no_tls, tmp_path):
    remote = RemoteCore(tmp_path / "core.sock")
    asyncio.run(remote._client().aclose())
    assert no_tls == []


def test_it_still_follows_the_core_reads_its_configuration_and_changes_it(core, sock_dir, no_tls):
    async def go():
        server = make_server(core, sock_dir / "core.sock")
        await server.start()
        remote = RemoteCore(sock_dir / "core.sock")
        remote.start()
        try:
            deadline = asyncio.get_running_loop().time() + 5
            while not (remote.connected and remote._config is not None):
                assert asyncio.get_running_loop().time() < deadline, "never connected"
                await asyncio.sleep(0.02)
            await remote.change_config(tot_duration=77.0)
            return remote.saved_config.tot_duration
        finally:
            await remote.stop()
            await server.stop()

    assert asyncio.run(go()) == 77.0
    assert no_tls == []
