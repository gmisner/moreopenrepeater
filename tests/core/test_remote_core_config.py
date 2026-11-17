"""`RemoteCore` reads and changes the core's configuration (Stage 3 slice 7c-3b, #229).
A real control server, a real `RemoteCore`, no devices, no Pi."""
import asyncio
import json
import logging
import tempfile
from pathlib import Path

import pytest
from control_helpers import make_server
from fastapi.testclient import TestClient

from api import remote_core as remote_module
from api.app import create_app
from api.remote_core import RemoteCore
from api.service import RepeaterService
from api.service.core_handle import ConfigRefused, CoreUnavailable
from controller.config import RepeaterConfig

SECRET = "pin-4821-do-not-log"


@pytest.fixture(autouse=True)
def quick_retries(monkeypatch):
    monkeypatch.setattr(remote_module, "RETRY_DELAYS", (0.05, 0.1))


async def until(condition, timeout=5.0, interval=0.02):
    deadline = asyncio.get_running_loop().time() + timeout
    while not condition():
        assert asyncio.get_running_loop().time() < deadline, "timed out"
        await asyncio.sleep(interval)


def run(core, sock_dir, scenario):
    async def go():
        path = sock_dir / "core.sock"
        server = make_server(core, path)
        await server.start()
        remote = RemoteCore(path)
        remote.start()
        try:
            await until(lambda: remote.connected and remote._config is not None)
            return await scenario(remote, server)
        finally:
            await remote.stop()
            await server.stop()

    return asyncio.run(go())


def test_the_saved_configuration_is_read_when_the_core_is_reached(core, sock_dir):
    core.service.update_config(autopatch_incoming_pin=SECRET)

    async def scenario(remote, server):
        await until(lambda: remote.saved_config.autopatch_incoming_pin == SECRET)
        assert remote.saved_config.callsign == "W1AW"
        assert remote.config_version == server._config_version

    run(core, sock_dir, scenario)


def test_before_the_core_is_reached_the_configuration_is_unknown_not_made_up(sock_dir):
    remote = RemoteCore(sock_dir / "nothing.sock")
    with pytest.raises(CoreUnavailable):
        remote.saved_config
    with pytest.raises(CoreUnavailable):
        remote.config


def test_a_change_is_applied_by_the_core_and_read_back(core, sock_dir):
    async def scenario(remote, server):
        saved = await remote.change_config(tot_duration=111.0, hang_time=2.5)
        assert saved.tot_duration == 111.0 and saved.hang_time == 2.5
        assert remote.saved_config.tot_duration == 111.0  # the cache is already current
        assert core.service.saved_config.tot_duration == 111.0  # the core has it
        assert json.loads(core.state_path.read_text())["config"]["tot_duration"] == 111.0  # and saved it

    run(core, sock_dir, scenario)


def test_a_refused_change_raises_and_changes_nothing(core, sock_dir):
    before = core.service.saved_config

    async def scenario(remote, server):
        with pytest.raises(ConfigRefused) as caught:
            await remote.change_config(tot_duration=0)
        assert caught.value.code == "config.unsafe" and not caught.value.retryable
        with pytest.raises(ConfigRefused) as caught:
            await remote.change_config(callsign="N0CALL", nope=1)
        assert caught.value.code == "config.unknown_setting"
        assert core.service.saved_config == before  # not even the good half

    run(core, sock_dir, scenario)


def test_the_message_names_settings_and_never_values(core, sock_dir, caplog):
    caplog.set_level(logging.DEBUG)

    async def scenario(remote, server):
        await remote.change_config(autopatch_incoming_pin=SECRET)
        with pytest.raises(ConfigRefused) as caught:
            await remote.change_config(autopatch_incoming_pin=12345, tot_duration=0)
        assert SECRET not in caught.value.message

    run(core, sock_dir, scenario)
    assert SECRET not in caplog.text


def test_a_change_made_elsewhere_reaches_the_cache(core, sock_dir):
    async def scenario(remote, server):
        core.service.update_config(tot_duration=77.0)  # a macro, or another client
        await until(lambda: remote.saved_config.tot_duration == 77.0)

    run(core, sock_dir, scenario)


def test_a_change_announced_while_the_configuration_is_being_read_is_not_lost(core, sock_dir):
    """The first read after the announcement can return the configuration from just before the
    change. The cache must notice it is behind and read again, not wait for the next status."""

    async def scenario(remote, server):
        real = server._get["/config"]
        stale = real()
        reads = []

        def first_read_is_stale():
            reads.append(1)
            return stale if len(reads) == 1 else real()

        server._get["/config"] = first_read_is_stale
        core.service.update_config(tot_duration=77.0)
        await until(lambda: remote.saved_config.tot_duration == 77.0)
        assert len(reads) >= 2

    run(core, sock_dir, scenario)


def test_the_effective_configuration_follows_net_mode(core, sock_dir):
    core.service.update_config(tot_duration=100.0, net_tot_duration=500.0)

    async def scenario(remote, server):
        await until(lambda: remote.saved_config.net_tot_duration == 500.0)
        assert remote.config.tot_duration == 100.0
        core.service.set_net_active(True)
        await until(lambda: remote.snapshot().net_active)
        assert remote.config.tot_duration == 500.0 and remote.saved_config.tot_duration == 100.0

    run(core, sock_dir, scenario)


def test_a_core_that_is_down_raises_and_says_nothing_was_changed(core, sock_dir):
    async def scenario(remote, server):
        await server.stop()
        with pytest.raises(CoreUnavailable) as caught:
            await remote.change_config(tot_duration=100.0)
        assert "nothing was changed" in str(caught.value)

    run(core, sock_dir, scenario)


def test_a_core_that_does_not_answer_raises_and_says_the_outcome_is_unknown(core, sock_dir, monkeypatch):
    monkeypatch.setattr(remote_module, "REQUEST_TIMEOUT_SECONDS", 0.3)

    async def scenario(remote, server):
        async def never(body):
            await asyncio.sleep(5)

        server._post["/config"] = never
        with pytest.raises(CoreUnavailable) as caught:
            await remote.change_config(tot_duration=100.0)
        assert "may or may not" in str(caught.value)

    run(core, sock_dir, scenario)


def test_an_answer_that_does_not_match_the_request_is_not_believed(core, sock_dir):
    async def scenario(remote, server):
        real = server._post["/config"]

        async def wrong(body):
            answer = await real(body)
            answer["header"]["request_id"] = "0" * 32
            return answer

        server._post["/config"] = wrong
        with pytest.raises(CoreUnavailable):
            await remote.change_config(tot_duration=100.0)

    run(core, sock_dir, scenario)


def test_a_conflicting_change_is_refused_as_retryable(core, sock_dir):
    async def scenario(remote, server):
        request_version = server._config_version
        core.service.update_config(tot_duration=88.0)
        # Sent the way a read-modify-write route would: with the version it read.
        from contracts import ConfigChange, Header, Source, new_id
        from datetime import datetime, timezone

        change = ConfigChange(
            header=Header(schema_version=1, message_id=new_id(), emitted_at=datetime.now(timezone.utc),
                          boot_id=new_id(), seq=1, source=Source(module="api", version="0")),
            values={"tot_duration": 99.0}, expected_version=request_version,
        )
        async with remote._client() as client:
            response = await client.post("/config", content=change.model_dump_json(), headers={"Content-Type": "application/json"})
        body = response.json()
        assert body["result"] == "rejected" and body["error"]["code"] == "config.conflict" and body["error"]["retryable"]

    run(core, sock_dir, scenario)


# -- the routes turn the two errors into answers ----------------------------------------------------


def make_client(error):
    service = RepeaterService(config=RepeaterConfig(callsign="W1AW"), clock=lambda: 0.0)

    async def change_config(**overrides):
        raise error

    service.change_config = change_config  # type: ignore[method-assign]
    tmp = Path(tempfile.mkdtemp())
    from api.assets import AudioAssetStore

    app = create_app(service=service, start_background_tick=False, assets_store=AudioAssetStore(tmp / "audio"), log_path=tmp / "t.log")
    return TestClient(app)


def test_a_refused_change_is_a_422_with_the_core_s_reason():
    client = make_client(ConfigRefused("config.unsafe", "not a usable number: tot_duration"))
    with client:
        response = client.put("/api/config", json={"tot_duration": 120})
    assert response.status_code == 422
    assert response.json() == {"detail": "not a usable number: tot_duration", "code": "config.unsafe"}


def test_an_unreachable_core_is_a_503_not_a_success():
    client = make_client(CoreUnavailable("the core is not reachable; nothing was changed"))
    with client:
        response = client.put("/api/config", json={"tot_duration": 120})
    assert response.status_code == 503
    assert response.json()["code"] == "core.unavailable" and "nothing was changed" in response.json()["detail"]


# -- commands (slice 7c-4a) ---------------------------------------------------------------------


def run_with(core, sock_dir, scenario, **server_kwargs):
    async def go():
        path = sock_dir / "core.sock"
        server = make_server(core, path, **server_kwargs)
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


def test_a_command_is_done_by_the_core_and_the_status_follows(core, sock_dir):
    async def scenario(remote, server):
        await remote.command("set_net_active", active=True)
        assert core.service.net_active is True
        await until(lambda: remote.snapshot().net_active)
        await remote.command("set_net_active", active=False)
        await until(lambda: not remote.snapshot().net_active)

    run_with(core, sock_dir, scenario)


def test_the_simulator_is_refused_by_a_core_on_a_real_radio(core, sock_dir):
    from api.service.core_handle import CommandRefused

    async def scenario(remote, server):
        with pytest.raises(CommandRefused) as caught:
            await remote.command("simulate_cos", active=True)
        assert caught.value.code == "command.simulation_disabled"
        assert core.service.cos_active is False

    run_with(core, sock_dir, scenario)


def test_the_simulator_works_when_the_core_allows_it(core, sock_dir):
    async def scenario(remote, server):
        await remote.command("simulate_ctcss", tone_hz=110.9)
        assert core.service.ctcss_hz == 110.9

    run_with(core, sock_dir, scenario, allow_simulate=True)


def test_a_bad_command_never_leaves_the_api(core, sock_dir):
    from api.service.core_handle import CommandRefused

    async def scenario(remote, server):
        with pytest.raises(CommandRefused) as caught:
            await remote.command("simulate_dtmf", digit="nope")
        assert caught.value.code == "command.invalid"
        with pytest.raises(CommandRefused):
            await remote.command("reboot")

    run_with(core, sock_dir, scenario, allow_simulate=True)


def test_a_command_to_a_core_that_is_down_says_nothing_was_done(core, sock_dir):
    async def scenario(remote, server):
        await server.stop()
        with pytest.raises(CoreUnavailable) as caught:
            await remote.command("clear_lockout")
        assert "nothing was changed" in str(caught.value)

    run_with(core, sock_dir, scenario)


def test_in_process_the_same_commands_work_and_unknown_ones_are_refused():
    import asyncio as aio

    from api.service.core_handle import CommandRefused

    service = RepeaterService(config=RepeaterConfig(callsign="W1AW"), clock=lambda: 0.0)

    async def go():
        await service.command("set_net_active", active=True)
        assert service.net_active is True
        with pytest.raises(CommandRefused):
            await service.command("reboot")

    aio.run(go())


def _client_with_command_error(error):
    service = RepeaterService(config=RepeaterConfig(callsign="W1AW"), clock=lambda: 0.0)

    async def command(name, **params):
        raise error

    service.command = command  # type: ignore[method-assign]
    tmp = Path(tempfile.mkdtemp())
    from api.assets import AudioAssetStore

    return TestClient(create_app(service=service, start_background_tick=False, assets_store=AudioAssetStore(tmp / "audio"), log_path=tmp / "t.log")), service


@pytest.mark.parametrize("code, status", [
    ("command.simulation_disabled", 403), ("command.apply_failed", 422), ("command.invalid", 422),
])
def test_a_refused_command_is_the_right_http_answer(code, status):
    from api.service.core_handle import CommandRefused

    client, _ = _client_with_command_error(CommandRefused(code, "no"))
    with client:
        response = client.post("/api/simulate/cos", json={"active": True})
    assert response.status_code == status and response.json()["code"] == code


def test_an_unreachable_core_is_a_503_for_a_command_too():
    client, _ = _client_with_command_error(CoreUnavailable("the core is not reachable; nothing was changed"))
    with client:
        response = client.post("/api/simulate/cos", json={"active": True})
    assert response.status_code == 503
