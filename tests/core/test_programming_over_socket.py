"""Macros and announcements over the control socket (Stage 3 slice 7c-4b, #229)."""
import asyncio

import pytest
from control_helpers import get, make_server

from api import remote_core as remote_module
from api.remote_core import RemoteCore
from api.service.core_handle import CommandRefused, CoreUnavailable
from contracts import ProgrammingState
from controller.announcements import Announcement
from controller.macros import Macro
from test_control_command import make_command, post_command


@pytest.fixture(autouse=True)
def quick_retries(monkeypatch):
    monkeypatch.setattr(remote_module, "RETRY_DELAYS", (0.05, 0.1))


async def until(condition, timeout=5.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not condition():
        assert asyncio.get_running_loop().time() < deadline, "timed out"
        await asyncio.sleep(0.02)


def run(core, sock_dir, scenario, **kwargs):
    async def go():
        path = sock_dir / "core.sock"
        server = make_server(core, path, **kwargs)
        await server.start()
        try:
            return await scenario(path, server)
        finally:
            await server.stop()

    return asyncio.run(go())


def run_remote(core, sock_dir, scenario, **kwargs):
    async def inner(path, server):
        remote = RemoteCore(path)
        remote.start()
        try:
            await until(lambda: remote._macros is not None)
            return await scenario(remote, server)
        finally:
            await remote.stop()

    return run(core, sock_dir, inner, **kwargs)


MACRO = {"pattern": "*71", "description": "net on", "command": "", "node_id": "", "action": "net_start"}
ANNOUNCEMENT = {"id": "ann1", "name": "Hourly", "message": "This is W1AW repeater", "kind": "interval", "every_minutes": 60}


# -- the endpoint ---------------------------------------------------------------------------


def test_get_programming_lists_macros_and_announcements_with_the_next_run(core, sock_dir):
    core.service.add_macro(Macro(**MACRO))
    core.service.save_announcement(Announcement(**ANNOUNCEMENT))

    async def scenario(path, server):
        return await get(path, "/programming")

    status, body = run(core, sock_dir, scenario)
    state = ProgrammingState.model_validate(body)
    assert status == 200 and [m["pattern"] for m in state.macros] == ["*71"]
    assert state.announcements[0]["id"] == "ann1" and state.announcements[0]["next_run"] is not None


def test_a_valid_macro_is_added_and_a_valid_announcement_saved(core, sock_dir):
    async def scenario(path, server):
        before = server._config_version
        a = await post_command(path, make_command("add_macro", macro=MACRO))
        b = await post_command(path, make_command("save_announcement", announcement=ANNOUNCEMENT))
        return a[1], b[1], before, server._config_version

    a, b, before, after = run(core, sock_dir, scenario)
    assert a["status"] == "applied" and b["status"] == "applied"
    assert [m.pattern for m in core.service.list_macros()] == ["*71"]
    assert [x.id for x in core.service.list_announcements()] == ["ann1"]
    assert after > before  # clients are told to read again


@pytest.mark.parametrize(
    "macro",
    [
        {"pattern": "abc", "description": "bad pattern"},  # not DTMF
        {"pattern": "*72", "action": "say", "command": ""},  # needs a command
        {"pattern": "*73", "action": "gpio", "command": "nonsense"},
        {"pattern": "*74", "action": "rm -rf"},
        {"pattern": "*75", "unknown_field": 1},
    ],
)
def test_the_core_validates_a_macro_with_the_api_s_own_rules(core, sock_dir, macro):
    async def scenario(path, server):
        return await post_command(path, make_command("add_macro", macro=macro))

    _, body = run(core, sock_dir, scenario)
    assert body["status"] == "rejected" and body["error"]["code"] == "command.invalid_macro"
    assert core.service.list_macros() == []


@pytest.mark.parametrize(
    "announcement",
    [
        {"name": "no id", "message": "x"},
        {"id": "a", "name": "", "message": "x"},
        {"id": "a", "name": "n", "message": ""},  # nothing to say
        {"id": "a", "name": "n", "message": "x", "kind": "weekly", "times": []},
        {"id": "a", "name": "n", "message": "x", "every_minutes": 0},
        {"id": "a", "name": "n", "message": "x", "kind": "weekly", "times": ["25:99"]},
        {"id": "a", "name": "n", "message": "x", "days": [9]},
    ],
)
def test_the_core_validates_an_announcement_with_the_api_s_own_rules(core, sock_dir, announcement):
    async def scenario(path, server):
        return await post_command(path, make_command("save_announcement", announcement=announcement))

    _, body = run(core, sock_dir, scenario)
    assert body["status"] == "rejected" and body["error"]["code"] == "command.invalid_announcement"
    assert core.service.list_announcements() == []


def test_an_announcement_that_names_a_missing_clip_is_refused(core, sock_dir):
    async def scenario(path, server):
        return await post_command(
            path, make_command("save_announcement", announcement={**ANNOUNCEMENT, "message": "", "asset_id": "nope"})
        )

    _, body = run(core, sock_dir, scenario, assets_have=lambda asset_id: False)
    assert body["error"]["code"] == "command.unknown_asset" and core.service.list_announcements() == []


def test_deleting_removes_them_and_a_missing_one_is_not_an_error(core, sock_dir):
    core.service.add_macro(Macro(**MACRO))
    core.service.save_announcement(Announcement(**ANNOUNCEMENT))

    async def scenario(path, server):
        results = [
            await post_command(path, make_command("delete_macro", pattern="*71")),
            await post_command(path, make_command("delete_announcement", announcement_id="ann1")),
            await post_command(path, make_command("delete_macro", pattern="*99")),
        ]
        return [r[1]["status"] for r in results]

    assert run(core, sock_dir, scenario) == ["applied"] * 3
    assert core.service.list_macros() == [] and core.service.list_announcements() == []


def test_programming_commands_do_not_need_the_simulator_flag(core, sock_dir):
    async def scenario(path, server):
        return (await post_command(path, make_command("add_macro", macro=MACRO)))[1]["status"]

    assert run(core, sock_dir, scenario, allow_simulate=False) == "applied"


def test_the_changes_are_saved_to_the_state_file(core, sock_dir):
    import json

    async def scenario(path, server):
        await post_command(path, make_command("add_macro", macro=MACRO))
        await post_command(path, make_command("save_announcement", announcement=ANNOUNCEMENT))

    run(core, sock_dir, scenario)
    saved = json.loads(core.state_path.read_text())
    assert [m["pattern"] for m in saved["macros"]] == ["*71"]
    assert [a["id"] for a in saved["announcements"]] == ["ann1"]


# -- RemoteCore -------------------------------------------------------------------------------


def test_the_lists_are_unknown_until_the_core_is_reached(sock_dir):
    remote = RemoteCore(sock_dir / "nothing.sock")
    with pytest.raises(CoreUnavailable):
        remote.list_macros()
    with pytest.raises(CoreUnavailable):
        remote.list_announcements()


def test_the_remote_core_reads_the_lists_and_changes_them_through_commands(core, sock_dir):
    core.service.save_announcement(Announcement(**ANNOUNCEMENT))

    async def scenario(remote, server):
        assert remote.list_macros() == []
        assert [a.id for a in remote.list_announcements()] == ["ann1"]
        assert remote.next_announcement_run("ann1") is not None
        assert remote.next_announcement_run("nope") is None
        await remote.command("add_macro", macro=MACRO)
        assert [m.pattern for m in remote.list_macros()] == ["*71"]  # already current, no waiting
        changed = {**ANNOUNCEMENT, "name": "Renamed"}
        await remote.command("save_announcement", announcement=changed)
        assert remote.list_announcements()[0].name == "Renamed"
        await remote.command("delete_announcement", announcement_id="ann1")
        await remote.command("delete_macro", pattern="*71")
        assert remote.list_announcements() == [] and remote.list_macros() == []

    run_remote(core, sock_dir, scenario)


def test_a_refused_macro_raises_and_the_lists_do_not_change(core, sock_dir):
    async def scenario(remote, server):
        with pytest.raises(CommandRefused) as caught:
            await remote.command("add_macro", macro={"pattern": "zzz"})
        assert caught.value.code == "command.invalid_macro" and remote.list_macros() == []

    run_remote(core, sock_dir, scenario)


def test_a_change_made_elsewhere_reaches_the_remote_lists(core, sock_dir):
    async def scenario(remote, server):
        core.service.add_macro(Macro(**MACRO))
        core.service._config_changed()  # whatever announces a change (restore does)
        await until(lambda: [m.pattern for m in remote.list_macros()] == ["*71"])

    run_remote(core, sock_dir, scenario)


def test_the_clip_for_an_announcement_matches_the_core_s(core, sock_dir):
    remote = RemoteCore(sock_dir / "x.sock")
    for announcement in (Announcement(id="a", name="n", message="hello"), Announcement(id="b", name="n", asset_id="abc")):
        assert remote.announcement_clip(announcement) == core.service.announcement_clip(announcement)
