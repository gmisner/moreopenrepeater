"""POST /command on the core's control socket (Stage 3 slice 7c-4a, #229). Simulation only."""
import asyncio
import logging
from datetime import datetime, timezone

import pytest
from control_helpers import make_server, parse, raw

from contracts import CommandResult, CoreCommand, Header, Source, new_id


def make_command(command, **params):
    return CoreCommand(
        header=Header(
            schema_version=1, message_id=new_id(), emitted_at=datetime.now(timezone.utc), boot_id=new_id(), seq=1,
            source=Source(module="api", version="0.1.0"),
        ),
        command=command, params=params,
    )


async def post_command(path, body):
    payload = body if isinstance(body, bytes) else body.model_dump_json().encode()
    request = (
        b"POST /command HTTP/1.1\r\nHost: core\r\nContent-Type: application/json\r\nContent-Length: "
        + str(len(payload)).encode() + b"\r\n\r\n" + payload
    )
    return parse(await raw(path, request))


def run(core, sock_dir, scenario, **kwargs):
    async def go():
        server = make_server(core, sock_dir / "core.sock", **kwargs)
        await server.start()
        try:
            return await scenario(sock_dir / "core.sock")
        finally:
            await server.stop()

    return asyncio.run(go())


def test_the_simulator_is_refused_on_a_core_that_was_not_told_to_allow_it(core, sock_dir):
    async def scenario(path):
        return await post_command(path, make_command("simulate_cos", active=True))

    status, body = run(core, sock_dir, scenario)
    assert status == 200 and body["status"] == "rejected" and body["error"]["code"] == "command.simulation_disabled"
    assert core.service.cos_active is False and core.service.controller.state == "idle"  # nothing was injected


@pytest.mark.parametrize("command, params", [
    ("simulate_ctcss", {"tone_hz": 100.0}), ("simulate_dtmf", {"digit": "5"}),
    ("simulate_remote_keyed", {"node_id": "41520", "keyed": True}),
])
def test_every_simulated_command_is_refused_without_the_flag(core, sock_dir, command, params):
    async def scenario(path):
        return await post_command(path, make_command(command, **params))

    _, body = run(core, sock_dir, scenario)
    assert body["error"]["code"] == "command.simulation_disabled"


def test_with_simulation_allowed_the_commands_act(core, sock_dir):
    async def scenario(path):
        a = await post_command(path, make_command("simulate_cos", active=True))
        state_while_keyed = core.service.cos_active
        b = await post_command(path, make_command("simulate_cos", active=False))
        c = await post_command(path, make_command("simulate_ctcss", tone_hz=123.0))
        return a, b, c, state_while_keyed

    a, b, c, keyed = run(core, sock_dir, scenario, allow_simulate=True)
    assert [x[1]["status"] for x in (a, b, c)] == ["applied"] * 3
    assert keyed is True and core.service.cos_active is False and core.service.ctcss_hz == 123.0


def test_net_mode_can_be_set_without_the_simulator_flag(core, sock_dir):
    async def scenario(path):
        on = await post_command(path, make_command("set_net_active", active=True))
        was_on = core.service.net_active
        off = await post_command(path, make_command("set_net_active", active=False))
        return on, off, was_on

    on, off, was_on = run(core, sock_dir, scenario)
    assert on[1]["status"] == "applied" and off[1]["status"] == "applied"
    assert was_on is True and core.service.net_active is False


def test_clearing_a_lockout_that_is_not_there_is_refused(core, sock_dir):
    async def scenario(path):
        return await post_command(path, make_command("clear_lockout"))

    _, body = run(core, sock_dir, scenario)
    assert body["status"] == "rejected" and body["error"]["code"] == "command.not_locked_out"


def test_a_real_lockout_is_cleared(core, sock_dir, monkeypatch):
    cleared = []
    monkeypatch.setattr(type(core.service), "locked_out", property(lambda self: True))
    monkeypatch.setattr(core.service, "clear_lockout", lambda: cleared.append(True))

    async def scenario(path):
        return await post_command(path, make_command("clear_lockout"))

    _, body = run(core, sock_dir, scenario)
    assert body["status"] == "applied" and cleared == [True]


def test_the_answer_points_back_at_the_command(core, sock_dir):
    request = make_command("set_net_active", active=False)

    async def scenario(path):
        return await post_command(path, request)

    _, body = run(core, sock_dir, scenario)
    assert CommandResult.model_validate(body).header.request_id == request.header.message_id


def test_malformed_commands_are_a_400_and_do_nothing(core, sock_dir):
    async def scenario(path):
        return [
            (await post_command(path, b"{not json"))[0],
            (await post_command(path, b'{"command": "reboot"}'))[0],
            (await post_command(path, make_command("set_net_active", active=True).model_dump_json().replace("true", '"yes"').encode()))[0],
        ]

    assert run(core, sock_dir, scenario) == [400, 400, 400]
    assert core.service.net_active is False


def test_a_failure_in_the_service_is_contained(core, sock_dir, monkeypatch):
    def boom(active):
        raise RuntimeError("controller exploded")

    monkeypatch.setattr(core.service, "set_net_active", boom)

    async def scenario(path):
        reply = await post_command(path, make_command("set_net_active", active=True))
        return reply, await post_command(path, make_command("set_net_active", active=False))

    (_, first), (_, second) = run(core, sock_dir, scenario)
    assert first["error"]["code"] == "command.apply_failed" and first["error"]["retryable"]
    assert second["error"]["code"] == "command.apply_failed"  # still answering


def test_the_control_server_logs_the_command_but_not_its_parameters(core, sock_dir, caplog):
    caplog.set_level(logging.DEBUG)

    async def scenario(path):
        return await post_command(path, make_command("simulate_remote_keyed", node_id="SECRETNODE", keyed=True))

    run(core, sock_dir, scenario, allow_simulate=True)
    ours = " ".join(r.getMessage() for r in caplog.records if r.name == "moreopenrepeater.core.control")
    assert "core.command simulate_remote_keyed applied" in ours
    assert "SECRETNODE" not in ours
