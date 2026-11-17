"""Link activity over the control socket (Stage 3 slice 7c-6a, #229)."""
import asyncio

import pytest
from control_helpers import get, make_server

from api.remote_core import RemoteCore
from api.service import RepeaterService
from api.service.core_handle import CoreUnavailable
from contracts import LinkEvents
from controller.config import RepeaterConfig


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


def key_some_links(core):
    core.service.simulate_remote_keyed("41520", True)
    core.service.simulate_remote_keyed("41520", False)
    core.service.simulate_remote_keyed("53127", True)


def test_get_link_events_returns_the_core_s_buffer_newest_first(core, sock_dir):
    key_some_links(core)

    async def scenario(path, server):
        return await get(path, "/link-events")

    status, body = run(core, sock_dir, scenario)
    events = LinkEvents.model_validate(body).events
    assert status == 200 and len(events) >= 3
    assert events == core.service.links.recent_events(500)
    assert events[0]["node"] == "53127"  # the latest first


def test_the_remote_core_reads_them_and_honours_the_limit(core, sock_dir):
    key_some_links(core)

    async def scenario(path, server):
        remote = RemoteCore(path)
        everything = await remote.recent_link_events(50)
        two = await remote.recent_link_events(2)
        none = await remote.recent_link_events(0)
        return everything, two, none

    everything, two, none = run(core, sock_dir, scenario)
    assert two == everything[:2] and len(two) == 2 and none == []
    assert everything == core.service.links.recent_events(50)


def test_a_core_that_is_down_raises_unavailable(sock_dir):
    with pytest.raises(CoreUnavailable):
        asyncio.run(RemoteCore(sock_dir / "nothing.sock").recent_link_events())


def test_in_process_the_same_call_answers_from_the_service():
    service = RepeaterService(config=RepeaterConfig(callsign="W1AW"), clock=lambda: 0.0)
    service.simulate_remote_keyed("41520", True)
    events = asyncio.run(service.recent_link_events(10))
    assert events == service.links.recent_events(10) and events[0]["node"] == "41520"
