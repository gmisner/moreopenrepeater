"""Commands that act on the radio side's hardware, over the control socket (Stage 3, #229)."""
import asyncio
import logging

import pytest
from control_helpers import make_server
from pydantic import ValidationError
from test_control_command import make_command, post_command

from api.gpio import GpioError
from contracts import CoreCommand


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


@pytest.fixture
def calls(core, monkeypatch):
    seen = []
    monkeypatch.setattr(core.runtime.gpio, "set_output", lambda pin, on: seen.append(("gpio", pin, on)))
    monkeypatch.setattr(core.runtime.remote_base, "retune", lambda: seen.append(("retune",)))
    monkeypatch.setattr(core.runtime.autopatch, "dial", lambda number, actor: seen.append(("dial", number, actor)) or None)
    monkeypatch.setattr(core.runtime.autopatch, "hangup", lambda reason: seen.append(("hangup", reason)))
    return seen


def test_each_command_reaches_its_part(core, sock_dir, calls):
    async def scenario(path, server):
        out = []
        for command, params in [
            ("gpio_set_output", {"pin": 3, "on": True}), ("remote_base_retune", {}),
            ("autopatch_dial", {"number": "5551234", "actor": "alice"}), ("autopatch_hangup", {"reason": "hung up by alice"}),
        ]:
            out.append((await post_command(path, make_command(command, **params)))[1]["status"])
        return out

    assert run(core, sock_dir, scenario) == ["applied"] * 4  # and no simulator flag was needed
    assert calls == [("gpio", 3, True), ("retune",), ("dial", "5551234", "alice"), ("hangup", "hung up by alice")]


def test_the_core_s_own_refusals_come_back_in_its_words(core, sock_dir, monkeypatch):
    def no_output(pin, on):
        raise GpioError(f"GPIO{pin} isn't set up as an output.")

    monkeypatch.setattr(core.runtime.gpio, "set_output", no_output)
    monkeypatch.setattr(core.runtime.autopatch, "dial", lambda number, actor: "The transmitter is off.")

    async def scenario(path, server):
        a = (await post_command(path, make_command("gpio_set_output", pin=5, on=True)))[1]
        b = (await post_command(path, make_command("autopatch_dial", number="1", actor="x")))[1]
        return a, b

    a, b = run(core, sock_dir, scenario)
    assert a["status"] == "rejected" and a["error"]["code"] == "command.hardware_refused"
    assert a["error"]["message"] == "GPIO5 isn't set up as an output."
    assert b["error"]["code"] == "command.hardware_refused" and b["error"]["message"] == "The transmitter is off."


def test_a_real_core_with_no_cm108_refuses_a_gpio_command(core, sock_dir):
    async def scenario(path, server):
        return (await post_command(path, make_command("gpio_set_output", pin=1, on=True)))[1]

    body = run(core, sock_dir, scenario)
    assert body["error"]["code"] == "command.hardware_refused" and "CM108" in body["error"]["message"]


def test_an_unexpected_failure_is_contained(core, sock_dir, monkeypatch):
    def boom():
        raise RuntimeError("rig exploded")

    monkeypatch.setattr(core.runtime.remote_base, "retune", boom)

    async def scenario(path, server):
        first = (await post_command(path, make_command("remote_base_retune")))[1]
        alive = (await post_command(path, make_command("set_net_active", active=False)))[1]
        return first, alive

    first, alive = run(core, sock_dir, scenario)
    assert first["error"]["code"] == "command.apply_failed" and first["error"]["retryable"]
    assert alive["status"] == "applied"


@pytest.mark.parametrize(
    "command, params",
    [
        ("gpio_set_output", {"pin": True, "on": True}),
        ("gpio_set_output", {"pin": -1, "on": True}),
        ("gpio_set_output", {"pin": 65, "on": True}),
        ("gpio_set_output", {"pin": "3", "on": True}),
        ("gpio_set_output", {"pin": 3, "on": 1}),
        ("remote_base_retune", {"x": 1}),
        ("autopatch_dial", {"number": "555-1234", "actor": "a"}),
        ("autopatch_dial", {"number": "1" * 16, "actor": "a"}),
        ("autopatch_dial", {"number": "", "actor": "a"}),
        ("autopatch_dial", {"number": "5551234", "actor": "bad\nactor"}),
        ("autopatch_dial", {"number": "5551234", "actor": ""}),
        ("autopatch_hangup", {"reason": "x" * 201}),
        ("autopatch_hangup", {}),
    ],
)
def test_malformed_hardware_commands_never_leave_the_contract(command, params):
    with pytest.raises(ValidationError):
        CoreCommand(header=make_command("clear_lockout").header, command=command, params=params)


def test_the_number_and_who_asked_are_not_in_the_core_s_log(core, sock_dir, calls, caplog):
    caplog.set_level(logging.DEBUG)

    async def scenario(path, server):
        await post_command(path, make_command("autopatch_dial", number="5559876543", actor="alice-the-admin"))

    run(core, sock_dir, scenario)
    ours = " ".join(r.getMessage() for r in caplog.records if r.name == "moreopenrepeater.core.control")
    assert "autopatch_dial applied" in ours and "5559876543" not in ours and "alice" not in ours
