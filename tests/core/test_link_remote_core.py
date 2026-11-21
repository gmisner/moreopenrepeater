"""`RemoteCore` carries the AllStar link service's view of the links to the core, in order and without ever
waiting on it (#221, #229)."""
import asyncio
import json

from control_helpers import make_server

from api import remote_core as remote_core_module
from api.remote_core import RemoteCore
from controller.events import LinkStateChanged, RemoteKeyed
from core.build import build_core


async def until(condition, timeout=5.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not condition():
        assert loop.time() < deadline, "timed out"
        await asyncio.sleep(0.01)


def run(core, sock_dir, scenario):
    async def go():
        path = sock_dir / "core.sock"
        server = make_server(core, path)
        await server.start()
        remote = RemoteCore(path)
        remote.start()
        try:
            await until(lambda: remote.connected)
            return await scenario(remote, server)
        finally:
            await remote.stop()
            await server.stop()

    return asyncio.run(go())


def test_events_reach_the_core_in_the_order_they_were_seen(core, sock_dir):
    async def scenario(remote, server):
        remote.link_event(LinkStateChanged(node_id="41520", linked=True))
        remote.link_event(RemoteKeyed(node_id="41520", keyed=True))
        remote.link_event(RemoteKeyed(node_id="41520", keyed=False))
        remote.link_event(RemoteKeyed(node_id="41520", keyed=True))
        await until(lambda: core.service.controller.remote_keyed_nodes == {"41520"})
        remote.link_event(LinkStateChanged(node_id="41520", linked=False))
        await until(lambda: not core.service.links.nodes)

    run(core, sock_dir, scenario)
    kinds = [e["kind"] for e in reversed(core.service.links.recent_events(20))]
    # a quick key, unkey, key is not collapsed into "keyed": the controller needs the unkey for its courtesy tone
    assert kinds == ["connected", "keyed", "unkeyed", "keyed", "unkeyed", "disconnected"]  # the last: implied by the disconnect
    assert not core.service.controller.remote_keyed_nodes


def test_a_sync_sets_the_picture(core, sock_dir):
    async def scenario(remote, server):
        remote.link_sync({"41520": True, "53127": False})
        await until(lambda: core.service.links.nodes == {"41520", "53127"})
        assert core.service.controller.remote_keyed_nodes == {"41520"}

    run(core, sock_dir, scenario)


def test_the_same_picture_is_not_sent_again_so_a_link_service_that_cannot_reach_app_rpt_does_not_repeat_itself(core, sock_dir, structured_events):
    async def scenario(remote, server):
        remote.link_sync({})
        await until(lambda: structured_events.named("link.synced"))
        for _ in range(5):  # every retry of the connection says "no links"
            remote.link_sync({})
        await asyncio.sleep(0.2)
        remote.link_sync({"41520": False})
        await until(lambda: core.service.links.nodes == {"41520"})

    run(core, sock_dir, scenario)
    assert len(structured_events.named("link.synced")) == 2  # the first {} and the one that changed


def test_a_core_that_restarts_and_forgets_is_given_the_picture_again(core, sock_dir, tmp_path):
    """A restarted core has no links. The picture the link service last gave is sent again once it is back."""
    (tmp_path / "second").mkdir()
    (tmp_path / "second" / "state.json").write_text(json.dumps({"config": {"callsign": "W1AW", "audio_enabled": False}}))
    fresh = build_core({"MOREOPENREPEATER_DATA_DIR": str(tmp_path / "second"), "MOREOPENREPEATER_LOG_PATH": str(tmp_path / "second" / "c.log")})

    async def go():
        path = sock_dir / "core.sock"
        server = make_server(core, path)
        await server.start()
        remote = RemoteCore(path)
        remote.start()
        try:
            await until(lambda: remote.connected)
            remote.link_sync({"41520": True})
            await until(lambda: core.service.links.nodes == {"41520"})
            await server.stop()
            await until(lambda: not remote.connected)
            assert fresh.service.links.nodes == set()  # the new core knows nothing
            server2 = make_server(fresh, path)
            await server2.start()
            try:
                await until(lambda: fresh.service.links.nodes == {"41520"})
                assert fresh.service.controller.remote_keyed_nodes == {"41520"}
            finally:
                await server2.stop()
        finally:
            await remote.stop()

    asyncio.run(go())


def test_events_seen_while_the_core_is_away_are_not_lost_but_summed_up_when_it_returns(core, sock_dir):
    async def scenario(remote, server):
        await server.stop()
        await until(lambda: not remote.connected)
        remote.link_event(LinkStateChanged(node_id="41520", linked=True))  # nobody to hear it; must not raise or wait
        remote.link_event(RemoteKeyed(node_id="41520", keyed=True))
        remote.link_event(LinkStateChanged(node_id="53127", linked=True))
        assert remote._link_state == {"41520": True, "53127": False}
        await server.start()
        await until(lambda: core.service.links.nodes == {"41520", "53127"})
        assert core.service.controller.remote_keyed_nodes == {"41520"}

    run(core, sock_dir, scenario)


def test_a_failed_send_means_the_next_event_brings_the_core_fully_up_to_date(core, sock_dir, monkeypatch):
    async def scenario(remote, server):
        real = remote.command
        failures = []

        async def flaky(name, **params):
            if not failures:
                failures.append(name)
                raise remote_core_module.CoreUnavailable("a blip")
            return await real(name, **params)

        monkeypatch.setattr(remote, "command", flaky)
        remote.link_event(LinkStateChanged(node_id="41520", linked=True))  # this one is lost
        await until(lambda: failures)
        await asyncio.sleep(0.1)
        assert core.service.links.nodes == set()
        remote.link_event(RemoteKeyed(node_id="41520", keyed=True))  # and this one carries the whole picture
        await until(lambda: core.service.links.nodes == {"41520"})
        assert core.service.controller.remote_keyed_nodes == {"41520"}

    run(core, sock_dir, scenario)


def test_a_flood_of_events_while_the_core_is_slow_is_cut_to_one_picture(core, sock_dir, monkeypatch):
    async def scenario(remote, server):
        monkeypatch.setattr(remote_core_module, "MAX_LINK_OUTBOX", 3)
        sent = []
        gate = asyncio.Event()

        async def slow(name, **params):
            sent.append((name, params))
            await gate.wait()

        monkeypatch.setattr(remote, "command", slow)
        remote.link_event(LinkStateChanged(node_id="1", linked=True))  # taken by the worker, which then waits
        await until(lambda: sent)
        for n in range(2, 12):  # ten more while it waits
            remote.link_event(LinkStateChanged(node_id=str(n), linked=True))
        assert len(remote._link_outbox) <= 3  # bounded
        gate.set()
        await until(lambda: not remote._link_outbox)
        await asyncio.sleep(0.05)
        last_name, last_params = sent[-1]
        assert last_name == "link_sync" and set(last_params["nodes"]) == {str(n) for n in range(1, 12)}  # nothing is lost

    run(core, sock_dir, scenario)


def test_with_no_event_loop_nothing_is_raised():
    remote = RemoteCore("/tmp/nothing.sock")
    remote.link_event(RemoteKeyed(node_id="41520", keyed=True))
    remote.link_sync({"41520": True})
    assert remote._link_state == {"41520": True}


def test_the_api_losing_and_finding_the_core_is_a_named_event_each_time_it_changes(structured_events):
    remote = RemoteCore("/tmp/nothing.sock")
    remote._set_connected(True)
    remote._set_connected(True)  # no change, no event
    remote._set_connected(False)
    changed = structured_events.named("core.connection_changed")
    assert [(e.context, e.message) for e in changed] == [
        ({"connected": True}, "core connected"), ({"connected": False}, "core not reachable"),
    ]
