"""The AllStar link service's commands on the core's control socket (#221, #229): `link_event` and `link_sync`."""
import asyncio
from datetime import datetime, timezone

import pytest
from control_helpers import make_server, parse, raw

from contracts import CoreCommand, Header, Source, new_id
from controller.events import LinkStateChanged, RemoteKeyed


def make_command(command, **params):
    return CoreCommand(
        header=Header(
            schema_version=1, message_id=new_id(), emitted_at=datetime.now(timezone.utc), boot_id=new_id(), seq=1,
            source=Source(module="api", version="0.1.0"),
        ),
        command=command, params=params,
    )


async def post(path, command):
    payload = command.model_dump_json().encode()
    request = (
        b"POST /command HTTP/1.1\r\nHost: core\r\nContent-Type: application/json\r\nContent-Length: "
        + str(len(payload)).encode() + b"\r\n\r\n" + payload
    )
    return parse(await raw(path, request))


def run(core, sock_dir, *commands, **server_kwargs):
    async def go():
        server = make_server(core, sock_dir / "core.sock", **server_kwargs)
        await server.start()
        try:
            return [await post(sock_dir / "core.sock", command) for command in commands]
        finally:
            await server.stop()

    return asyncio.run(go())


def sync(**nodes):
    return make_command("link_sync", nodes=nodes)


def test_link_events_are_applied_on_a_core_that_refuses_the_simulator(core, sock_dir):
    """They are real events from a real node, not simulated ones, so `allow_simulate=False` does not stop them."""
    results = run(
        core, sock_dir,
        make_command("link_event", node_id="41520", event="connected"),
        make_command("link_event", node_id="41520", event="keyed"),
        allow_simulate=False,
    )
    assert [r[1]["status"] for r in results] == ["applied", "applied"]
    assert core.service.links.nodes == {"41520"}
    assert core.service.controller.remote_keyed_nodes == {"41520"}
    assert core.service.controller.state == "receiving"


def test_unkeyed_and_disconnected_undo_them(core, sock_dir):
    run(
        core, sock_dir,
        make_command("link_event", node_id="41520", event="connected"),
        make_command("link_event", node_id="41520", event="keyed"),
        make_command("link_event", node_id="41520", event="unkeyed"),
        make_command("link_event", node_id="41520", event="disconnected"),
    )
    assert core.service.links.nodes == set() and not core.service.controller.remote_keyed_nodes


def test_the_events_show_in_the_cores_link_activity(core, sock_dir):
    run(core, sock_dir, make_command("link_event", node_id="41520", event="connected"), make_command("link_event", node_id="41520", event="keyed"))
    assert [(e["kind"], e["node"]) for e in core.service.links.recent_events(5)] == [("keyed", "41520"), ("connected", "41520")]


def test_a_sync_brings_up_the_nodes_it_lists_and_keys_the_ones_that_are_keyed(core, sock_dir):
    [(status, body)] = run(core, sock_dir, sync(**{"41520": False, "53127": True}))
    assert body["status"] == "applied"
    assert core.service.links.nodes == {"41520", "53127"}
    assert core.service.controller.remote_keyed_nodes == {"53127"}


def test_a_sync_removes_the_nodes_it_does_not_list_and_unkeys_them_first(core, sock_dir):
    """A node that was transmitting when the link service lost app_rpt must not leave the core 'receiving'."""
    run(
        core, sock_dir,
        make_command("link_event", node_id="41520", event="connected"),
        make_command("link_event", node_id="41520", event="keyed"),
    )
    assert core.service.controller.state == "receiving"
    seen = []
    original = core.service.controller.handle_event
    core.service.controller.handle_event = lambda event, now: (seen.append(event), original(event, now))[1]
    run(core, sock_dir, sync())
    assert seen[0] == RemoteKeyed(node_id="41520", keyed=False) and seen[1] == LinkStateChanged(node_id="41520", linked=False)
    assert core.service.links.nodes == set() and not core.service.controller.remote_keyed_nodes
    assert core.service.controller.state != "receiving"


def test_a_sync_changes_only_what_differs(core, sock_dir):
    run(core, sock_dir, sync(**{"41520": False, "53127": True}))
    seen = []
    original = core.service.controller.handle_event
    core.service.controller.handle_event = lambda event, now: (seen.append(event), original(event, now))[1]
    run(core, sock_dir, sync(**{"41520": True, "53127": True, "60001": False}))
    assert seen == [LinkStateChanged(node_id="60001", linked=True), RemoteKeyed(node_id="41520", keyed=True)]


def test_a_sync_leaves_the_nodes_audio_and_other_bridges_alone(core, sock_dir):
    """USRP and the DMR and linked-radio bridges key themselves; the link service does not speak for them."""
    from api.service.snapshot import LINK_AUDIO_NODE, LINK_DMR_NODE

    core.service.simulate_remote_keyed(LINK_AUDIO_NODE, True)
    core.service.simulate_remote_keyed(LINK_DMR_NODE, True)
    run(core, sock_dir, sync())
    assert {LINK_AUDIO_NODE, LINK_DMR_NODE} <= core.service.links.nodes
    assert {LINK_AUDIO_NODE, LINK_DMR_NODE} <= core.service.controller.remote_keyed_nodes


def test_a_sync_is_one_event_in_the_log_with_counts_and_no_node_names(core, sock_dir, structured_events):
    run(core, sock_dir, sync(**{"41520": True, "53127": False}))
    [synced] = structured_events.named("link.synced")
    assert synced.context == {"nodes": 2, "keyed": 1} and synced.severity == 20
    assert "41520" not in synced.model_dump_json() and "53127" not in synced.model_dump_json()


@pytest.mark.parametrize("event", ["keyed", "unkeyed"])
def test_a_single_event_is_not_logged_each_time(core, sock_dir, structured_events, event):
    run(core, sock_dir, make_command("link_event", node_id="41520", event=event))
    assert structured_events.named("command.applied") == []  # a node keying is routine, not a command worth a line


def test_a_core_that_cannot_apply_one_says_so_and_does_not_crash(core, sock_dir, structured_events, monkeypatch):
    def broken(nodes):
        raise RuntimeError("private detail")

    monkeypatch.setattr(core.service, "link_sync", broken)
    [(status, body)] = run(core, sock_dir, sync(**{"41520": False}))
    assert body["status"] == "rejected" and body["error"]["code"] == "command.apply_failed"
    [failed] = structured_events.named("command.failed")
    assert failed.context == {"command": "link_sync", "exception": "RuntimeError"} and "private detail" not in failed.model_dump_json()


def test_a_node_that_disconnects_while_keyed_is_unkeyed_even_if_nobody_said_so(core, sock_dir):
    """The real client reports the unkey first, but the core does not depend on it: a node that is no longer
    linked cannot be transmitting to us."""
    run(
        core, sock_dir,
        make_command("link_event", node_id="41520", event="connected"),
        make_command("link_event", node_id="41520", event="keyed"),
    )
    assert core.service.controller.state == "receiving"
    run(core, sock_dir, make_command("link_event", node_id="41520", event="disconnected"))
    assert not core.service.controller.remote_keyed_nodes and core.service.links.nodes == set()
    assert core.service.controller.state != "receiving"
