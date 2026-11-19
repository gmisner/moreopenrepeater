"""`usrp_configure`: the dashboard tells the core where the AllStar node or the DMR bridge is (container install)."""
import asyncio
import json

import pytest
from control_helpers import make_server
from pydantic import ValidationError
from test_control_command import make_command, post_command

from api.usrp_endpoints import UsrpEndpoints
from core.build import build_core

NODE = {"host": "127.0.0.1", "port": 32001}


def build(tmp_path, **env):
    (tmp_path / "state.json").write_text(json.dumps({"config": {"callsign": "W1AW", "audio_enabled": False}}))
    return build_core({
        "MOREOPENREPEATER_DATA_DIR": str(tmp_path), "MOREOPENREPEATER_LOG_PATH": str(tmp_path / "c.log"),
        "MOREOPENREPEATER_USRP_LISTEN": "127.0.0.1:0", "MOREOPENREPEATER_DMR_USRP_LISTEN": "127.0.0.1:0", **env,
    })


def run(core, sock_dir, scenario):
    async def go():
        path = sock_dir / "core.sock"
        server = make_server(core, path)
        await server.start()
        try:
            return await scenario(path)
        finally:
            await server.stop()
            await core.runtime.allstar_node.stop()
            await core.runtime.dmr_audio.stop()

    return asyncio.run(go())


def send(core, sock_dir, **params):
    async def scenario(path):
        return (await post_command(path, make_command("usrp_configure", **params)))[1]

    return run(core, sock_dir, scenario)


def test_the_allstar_audio_is_pointed_at_the_node_and_stopped(tmp_path, sock_dir):
    core = build(tmp_path)

    async def scenario(path):
        applied = (await post_command(path, make_command("usrp_configure", service="allstar", peer=NODE)))[1]
        running = core.runtime.allstar_node.audio.status()
        stopped = (await post_command(path, make_command("usrp_configure", service="allstar", peer=None)))[1]
        return applied, running, stopped, core.runtime.allstar_node.audio.status()

    applied, running, stopped, after = run(core, sock_dir, scenario)
    assert applied["status"] == "applied" and running["configured"] and running["running"]
    assert running["node"] == "127.0.0.1:32001"
    assert stopped["status"] == "applied" and not after["configured"] and not after["running"]


def test_the_dmr_audio_is_pointed_at_the_bridge(tmp_path, sock_dir):
    core = build(tmp_path)

    async def scenario(path):
        body = (await post_command(path, make_command("usrp_configure", service="dmr", peer={"host": "127.0.0.1", "port": 34012})))[1]
        return body, core.runtime.dmr_audio.status()

    body, status = run(core, sock_dir, scenario)
    assert body["status"] == "applied" and status["configured"] and status["running"]


def test_what_was_set_is_kept_for_the_next_start_of_the_core(tmp_path, sock_dir):
    core = build(tmp_path)
    assert send(core, sock_dir, service="allstar", peer=NODE)["status"] == "applied"
    saved = json.loads((tmp_path / "usrp-endpoints.json").read_text())
    assert saved == {"allstar": NODE}  # a host and a port, nothing else

    again = build(tmp_path)
    assert UsrpEndpoints(tmp_path / "usrp-endpoints.json").load() == {"allstar": ("127.0.0.1", 32001)}

    async def start():
        await again.runtime.start_audio_and_links(asyncio.get_running_loop(), [])
        try:
            return again.runtime.allstar_node.audio.status()
        finally:
            await again.runtime.allstar_node.stop()
            await again.runtime.dmr_audio.stop()

    status = asyncio.run(start())
    assert status["configured"] and status["running"] and status["node"] == "127.0.0.1:32001"


def test_turning_one_off_forgets_it_and_leaves_the_other(tmp_path, sock_dir):
    core = build(tmp_path)
    send(core, sock_dir, service="allstar", peer=NODE)
    send(core, sock_dir, service="dmr", peer={"host": "127.0.0.1", "port": 34012})
    send(core, sock_dir, service="allstar", peer=None)
    assert json.loads((tmp_path / "usrp-endpoints.json").read_text()) == {"dmr": {"host": "127.0.0.1", "port": 34012}}


def test_a_node_set_in_the_environment_is_not_changed_from_the_dashboard(tmp_path, sock_dir):
    core = build(tmp_path, MOREOPENREPEATER_USRP_NODE="127.0.0.1:32009")
    body = send(core, sock_dir, service="allstar", peer=NODE)
    assert body["status"] == "rejected" and body["error"]["code"] == "command.hardware_refused"
    assert "MOREOPENREPEATER_USRP_NODE" in body["error"]["message"]
    assert not (tmp_path / "usrp-endpoints.json").exists()


def test_a_node_that_cannot_be_found_is_refused_in_words_and_not_kept(tmp_path, sock_dir):
    core = build(tmp_path)
    body = send(core, sock_dir, service="allstar", peer={"host": "no-such-host.invalid", "port": 32001})
    assert body["status"] == "rejected" and body["error"]["code"] == "command.hardware_refused"
    assert not (tmp_path / "usrp-endpoints.json").exists()


def test_the_core_s_own_event_says_which_service_and_not_where(tmp_path, sock_dir, structured_events):
    core = build(tmp_path)
    assert send(core, sock_dir, service="allstar", peer=NODE)["status"] == "applied"
    [event] = structured_events.named("usrp.configured")
    assert event.context == {"service": "allstar", "enabled": True}
    assert "32001" not in event.message and "127.0.0.1" not in event.message


@pytest.mark.parametrize("params", [
    {"service": "echolink", "peer": NODE},                       # not a service
    {"service": "allstar", "peer": {"host": "x"}},                # no port
    {"service": "allstar", "peer": {"host": "x", "port": 0}},     # not a port
    {"service": "allstar", "peer": {"host": "x", "port": 70000}},
    {"service": "allstar", "peer": {"host": "x", "port": True}},
    {"service": "allstar", "peer": {"host": "a b", "port": 5}},   # not a host
    {"service": "allstar", "peer": {"host": "x", "port": 5, "extra": 1}},
    {"service": "allstar", "peer": "127.0.0.1:32001"},
    {"service": "allstar"},
])
def test_the_command_checks_what_it_is_given(params):
    with pytest.raises(ValidationError):
        make_command("usrp_configure", **params)


def test_a_damaged_file_gives_nothing_not_a_crash(tmp_path):
    path = tmp_path / "usrp-endpoints.json"
    for text in ("", "not json", "[]", '{"allstar": "x"}', '{"allstar": {"host": "a b", "port": 1}}',
                 '{"allstar": {"host": "a", "port": 0}}', '{"allstar": {"host": "a", "port": true}}'):
        path.write_text(text)
        assert UsrpEndpoints(path).load() == {}
    assert UsrpEndpoints(None).load() == {}
    UsrpEndpoints(None).save("allstar", ("a", 1))  # nowhere to keep it: nothing happens
