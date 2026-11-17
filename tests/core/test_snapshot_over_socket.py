"""Backup export and restore through the core (Stage 3 slice 7c-5, #229)."""
import asyncio
import json
import logging

import pytest
from control_helpers import make_server, parse, raw
from test_control_command import make_command, post_command

from api import remote_core as remote_module
from api.remote_core import RemoteCore
from api.service.core_handle import CommandRefused, CoreUnavailable
from controller.announcements import Announcement
from controller.config import RepeaterConfig
from controller.macros import Macro
from core import control as control_module

PIN = "pin-7788-do-not-log"


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


def run_remote(core, sock_dir, scenario):
    async def inner(path, server):
        remote = RemoteCore(path)
        remote.start()
        try:
            await until(lambda: remote.connected and remote._macros is not None)
            return await scenario(remote, server)
        finally:
            await remote.stop()

    return run(core, sock_dir, inner)


def snapshot(**config):
    return {
        "config": {"callsign": "N0CALL", "tot_duration": 240, **config},
        "macros": [{"pattern": "*71", "description": "net on", "action": "net_start"}],
        "announcements": [{"id": "a1", "name": "Hourly", "message": "N0CALL repeater", "every_minutes": 30}],
    }


# -- the endpoint ------------------------------------------------------------------------------


def test_a_snapshot_replaces_everything_and_is_saved(core, sock_dir):
    core.service.add_macro(Macro(pattern="*99", description="old", action="time"))

    async def scenario(path, server):
        return (await post_command(path, make_command("import_snapshot", snapshot=snapshot())))[1]

    body = run(core, sock_dir, scenario)
    assert body["status"] == "applied"
    assert core.service.saved_config.callsign == "N0CALL" and core.service.saved_config.tot_duration == 240
    assert [m.pattern for m in core.service.list_macros()] == ["*71"]  # the old one is gone
    assert [a.id for a in core.service.list_announcements()] == ["a1"]
    saved = json.loads(core.state_path.read_text())
    assert saved["config"]["callsign"] == "N0CALL" and [m["pattern"] for m in saved["macros"]] == ["*71"]


def test_a_backup_from_an_older_version_gets_defaults_for_what_it_lacks(core, sock_dir):
    async def scenario(path, server):
        return (await post_command(path, make_command("import_snapshot", snapshot={"config": {"callsign": "N0CALL"}})))[1]

    assert run(core, sock_dir, scenario)["status"] == "applied"
    assert core.service.saved_config.tot_duration == RepeaterConfig().tot_duration
    assert core.service.list_macros() == [] and core.service.list_announcements() == []


@pytest.mark.parametrize(
    "bad, code",
    [
        ({"config": {"callsign": "N0CALL", "tot_duration": "soon"}}, "command.invalid_snapshot"),
        ({"config": {"callsign": "N0CALL"}, "macros": [{"pattern": "zzz"}]}, "command.invalid_snapshot"),
        ({"config": {"callsign": "N0CALL"}, "announcements": [{"id": "a", "name": "n"}]}, "command.invalid_snapshot"),
        ({"macros": []}, "command.invalid_snapshot"),  # no config at all
        ({"config": {"callsign": ""}}, "command.unsafe_snapshot"),
        ({"config": {"callsign": "N0CALL", "tot_duration": 0}}, "command.unsafe_snapshot"),
        ({"config": {"callsign": "N0CALL", "hang_time": -3}}, "command.unsafe_snapshot"),
    ],
)
def test_a_bad_snapshot_is_refused_and_applies_nothing(core, sock_dir, bad, code):
    core.service.add_macro(Macro(pattern="*99", description="keep me", action="time"))
    before = core.service.saved_config

    async def scenario(path, server):
        return (await post_command(path, make_command("import_snapshot", snapshot=bad)))[1]

    body = run(core, sock_dir, scenario)
    assert body["status"] == "rejected" and body["error"]["code"] == code
    assert core.service.saved_config == before
    assert [m.pattern for m in core.service.list_macros()] == ["*99"]


def test_the_snapshot_is_never_logged_or_echoed(core, sock_dir, caplog):
    caplog.set_level(logging.DEBUG)

    async def scenario(path, server):
        return (await post_command(path, make_command("import_snapshot", snapshot=snapshot(autopatch_incoming_pin=PIN))))[1]

    body = run(core, sock_dir, scenario)
    assert body["status"] == "applied" and PIN not in json.dumps(body)
    assert PIN not in caplog.text


def test_a_large_snapshot_is_accepted_and_a_huge_one_is_not(core, sock_dir):
    many = snapshot()
    many["macros"] = [{"pattern": f"*{i}", "description": "d" * 40, "action": "time"} for i in range(100, 500)]

    async def scenario(path, server):
        ok = (await post_command(path, make_command("import_snapshot", snapshot=many)))
        # Refused from the headers alone, before any of the body is read.
        size = control_module.MAX_COMMAND_BODY_BYTES + 10
        huge = parse(await raw(
            path, b"POST /command HTTP/1.1\r\nHost: core\r\nContent-Type: application/json\r\nContent-Length: "
            + str(size).encode() + b"\r\n\r\n"))
        return ok, huge

    ok, huge = run(core, sock_dir, scenario)
    assert len(json.dumps(many)) > control_module.MAX_BODY_BYTES  # bigger than the default limit
    assert ok[1]["status"] == "applied" and len(core.service.list_macros()) == 400
    assert huge[0] == 413


def test_a_failure_in_the_service_is_contained(core, sock_dir, monkeypatch):
    def boom(data):
        raise RuntimeError("disk full")

    monkeypatch.setattr(core.service, "import_snapshot", boom)

    async def scenario(path, server):
        reply = (await post_command(path, make_command("import_snapshot", snapshot=snapshot())))[1]
        return reply, (await post_command(path, make_command("set_net_active", active=False)))[1]

    reply, alive = run(core, sock_dir, scenario)
    assert reply["error"]["code"] == "command.apply_failed" and alive["status"] == "applied"


# -- RemoteCore ----------------------------------------------------------------------------------


def test_the_export_has_the_shape_a_backup_stores(core, sock_dir):
    core.service.add_macro(Macro(pattern="*71", description="d", action="net_start"))
    core.service.save_announcement(Announcement(id="a1", name="n", message="m"))

    async def scenario(remote, server):
        exported = remote.export_snapshot()
        assert set(exported) == {"config", "macros", "announcements"}
        assert exported == core.service.export_snapshot()  # exactly what the core itself would export

    run_remote(core, sock_dir, scenario)


def test_nothing_is_exported_from_a_core_that_is_not_being_heard(core, sock_dir):
    async def scenario(remote, server):
        await server.stop()
        remote._connected = False
        with pytest.raises(CoreUnavailable):
            remote.export_snapshot()

    run_remote(core, sock_dir, scenario)
    with pytest.raises(CoreUnavailable):
        RemoteCore(sock_dir / "never.sock").export_snapshot()


def test_a_restore_through_the_remote_core_is_read_back_at_once(core, sock_dir):
    async def scenario(remote, server):
        await remote.command("import_snapshot", snapshot=snapshot(autopatch_incoming_pin=PIN))
        exported = remote.export_snapshot()  # no waiting: the command read it back
        assert exported["config"]["callsign"] == "N0CALL" and exported["config"]["autopatch_incoming_pin"] == PIN
        assert [m["pattern"] for m in exported["macros"]] == ["*71"]
        assert remote.saved_config.tot_duration == 240

    run_remote(core, sock_dir, scenario)


def test_a_refused_restore_raises_and_the_export_is_unchanged(core, sock_dir):
    async def scenario(remote, server):
        before = remote.export_snapshot()
        with pytest.raises(CommandRefused) as caught:
            await remote.command("import_snapshot", snapshot={"config": {"callsign": ""}})
        assert caught.value.code == "command.unsafe_snapshot"
        assert remote.export_snapshot() == before

    run_remote(core, sock_dir, scenario)


# -- the shared validation --------------------------------------------------------------------------


def test_the_core_and_the_dashboard_use_one_validation():
    import importlib

    from api import snapshot_import

    app_module = importlib.import_module("api.app")
    from api.models import SnapshotImportRequest

    request = SnapshotImportRequest.model_validate(snapshot())
    assert app_module._snapshot_to_import(request) == snapshot_import.snapshot_to_import(request)
