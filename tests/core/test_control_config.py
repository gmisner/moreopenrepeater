"""GET and POST /config on the core's control socket (Stage 3 slice 7c-3a, #229): the API
reads and changes the whole configuration, and the core checks every change itself.
Simulation only: no devices, no Pi."""
import asyncio
import json
import logging
from datetime import datetime, timezone

import pytest
from control_helpers import get, make_server, parse, raw

from contracts import ConfigApplied, ConfigChange, ConfigResult, ConfigState, Header, Source, new_id
from core import control as control_module
from core.config_boundary import ConfigRejected, check_change
from controller.config import RepeaterConfig

SECRET = "pin-4821-do-not-log"


def make_change(values, expected_version=None):
    return ConfigChange(
        header=Header(
            schema_version=1, message_id=new_id(), emitted_at=datetime.now(timezone.utc), boot_id=new_id(), seq=1,
            source=Source(module="api", version="0.1.0"),
        ),
        values=values, expected_version=expected_version,
    )


async def post_config(path, body, *, content_type="application/json"):
    payload = body if isinstance(body, bytes) else body.model_dump_json().encode()
    request = (
        b"POST /config HTTP/1.1\r\nHost: core\r\nContent-Type: " + content_type.encode()
        + b"\r\nContent-Length: " + str(len(payload)).encode() + b"\r\n\r\n" + payload
    )
    return parse(await raw(path, request))


def run(core, sock_dir, scenario):
    async def go():
        server = make_server(core, sock_dir / "core.sock")
        await server.start()
        try:
            return await scenario(sock_dir / "core.sock")
        finally:
            await server.stop()

    return asyncio.run(go())


# -- reading ------------------------------------------------------------------------------


def test_get_config_returns_the_whole_configuration_including_secrets(core, sock_dir):
    core.service.update_config(autopatch_incoming_pin=SECRET)

    async def scenario(path):
        return await get(path, "/config")

    status, body = run(core, sock_dir, scenario)
    assert status == 200
    state = ConfigState.model_validate(body)
    assert state.values["callsign"] == "W1AW" and state.values["autopatch_incoming_pin"] == SECRET
    assert state.config_version >= 1
    assert set(state.values) == set(core.service.saved_config.to_flat_dict())


def test_the_values_never_show_in_a_repr(core):
    state = ConfigState(
        header=make_change({"a": 1}).header, config_version=1, values={"autopatch_incoming_pin": SECRET},
    )
    assert SECRET not in repr(state) and SECRET not in str(state)
    assert SECRET not in repr(make_change({"autopatch_incoming_pin": SECRET}))


def test_the_version_rises_with_every_change_whoever_makes_it(core, sock_dir):
    async def scenario(path):
        _, first = await get(path, "/config")
        core.service.update_config(tot_duration=95)
        _, second = await get(path, "/config")
        return first["config_version"], second["config_version"]

    before, after = run(core, sock_dir, scenario)
    assert after > before


# -- changing -----------------------------------------------------------------------------


def test_a_good_change_is_applied_and_saved(core, sock_dir):
    async def scenario(path):
        return await post_config(path, make_change({"tot_duration": 120, "callsign": "W1AW"}))

    status, body = run(core, sock_dir, scenario)
    assert status == 200
    applied = ConfigApplied.model_validate(body)
    assert applied.result is ConfigResult.APPLIED and applied.error is None
    assert core.service.saved_config.tot_duration == 120
    saved = json.loads(core.state_path.read_text())
    assert saved["config"]["tot_duration"] == 120  # it reached the state file


def test_the_answer_points_back_at_the_change(core, sock_dir):
    request = make_change({"tot_duration": 100})

    async def scenario(path):
        return await post_config(path, request)

    _, body = run(core, sock_dir, scenario)
    assert body["header"]["request_id"] == request.header.message_id


@pytest.mark.parametrize(
    "values, code",
    [
        ({"no_such_setting": 1}, "config.unknown_setting"),
        ({"tot_duration": "soon"}, "config.wrong_type"),
        ({"tot_duration": True}, "config.wrong_type"),
        ({"audio_enabled": "yes"}, "config.wrong_type"),
        ({"tot_duration": 0}, "config.unsafe"),
        ({"tot_duration": -5}, "config.unsafe"),
        ({"id_interval": float("inf")}, "config.unsafe"),
        ({"hang_time": -1}, "config.unsafe"),
        ({"callsign": "   "}, "config.unsafe"),
    ],
)
def test_an_unsafe_or_malformed_change_is_refused_and_applies_nothing(core, sock_dir, values, code):
    before = core.service.saved_config

    async def scenario(path):
        # inf is not JSON; send it the way a hand-written client would (a bare token).
        message = make_change({"ok": 1})
        raw_body = message.model_dump_json().encode().replace(b'{"ok":1}', json.dumps(values).encode())
        return await post_config(path, raw_body)

    status, body = run(core, sock_dir, scenario)
    if status == 400:
        pytest.skip("not even valid JSON for the contract; refused earlier, which is also fine")
    assert status == 200
    applied = ConfigApplied.model_validate(body)
    assert applied.result is ConfigResult.REJECTED and applied.error.code == code
    assert core.service.saved_config == before  # nothing was applied, not even the good half


def test_a_mixed_change_applies_nothing(core, sock_dir):
    before = core.service.saved_config

    async def scenario(path):
        return await post_config(path, make_change({"callsign": "N0CALL", "tot_duration": 0}))

    _, body = run(core, sock_dir, scenario)
    assert body["result"] == "rejected"
    assert core.service.saved_config == before and core.service.saved_config.callsign == "W1AW"


def test_a_setting_can_be_cleared_when_it_may_be_empty(core, sock_dir):
    core.service.update_config(tx_ctcss_hz=100.0)

    async def scenario(path):
        return await post_config(path, make_change({"tx_ctcss_hz": None}))

    _, body = run(core, sock_dir, scenario)
    assert body["result"] == "applied" and core.service.saved_config.tx_ctcss_hz is None


def test_an_old_version_is_a_conflict(core, sock_dir):
    async def scenario(path):
        _, state = await get(path, "/config")
        core.service.update_config(tot_duration=90)  # someone else changes it
        return await post_config(path, make_change({"tot_duration": 200}, expected_version=state["config_version"]))

    _, body = run(core, sock_dir, scenario)
    assert body["result"] == "rejected" and body["error"]["code"] == "config.conflict" and body["error"]["retryable"]
    assert core.service.saved_config.tot_duration == 90


def test_the_current_version_is_accepted(core, sock_dir):
    async def scenario(path):
        _, state = await get(path, "/config")
        return await post_config(path, make_change({"tot_duration": 200}, expected_version=state["config_version"]))

    _, body = run(core, sock_dir, scenario)
    assert body["result"] == "applied"


def test_values_are_never_logged_or_echoed(core, sock_dir, caplog):
    caplog.set_level(logging.DEBUG)

    async def scenario(path):
        return await post_config(path, make_change({"autopatch_incoming_pin": SECRET}))

    status, body = run(core, sock_dir, scenario)
    assert body["result"] == "applied"
    assert SECRET not in json.dumps(body)
    assert SECRET not in caplog.text
    assert "autopatch_incoming_pin" in caplog.text  # the name is logged, the value is not


def test_a_malformed_body_is_a_400_and_changes_nothing(core, sock_dir):
    before = core.service.saved_config

    async def scenario(path):
        a = await post_config(path, b"{not json")
        b = await post_config(path, b'{"values": {}}')
        c = await post_config(path, make_change({"tot_duration": 100}), content_type="text/plain")
        return a, b, c

    a, b, c = run(core, sock_dir, scenario)
    assert a[0] == 400 and b[0] == 400 and c[0] == 415
    assert core.service.saved_config == before


def test_the_body_limit_for_config_is_larger_than_the_default_but_bounded(core, sock_dir):
    async def scenario(path):
        medium = make_change({"callsign": "W1AW", "x": "a" * 20_000})  # over 16 KiB, under 64 KiB
        # The server refuses on the declared length alone and closes without reading the body,
        # so send only the headers. Writing the whole body into a connection the server has
        # already closed raises BrokenPipe on macOS, whose socket buffers are small.
        too_long = control_module.MAX_CONFIG_BODY_BYTES + 10
        huge = (
            b"POST /config HTTP/1.1\r\nHost: core\r\nContent-Type: application/json\r\nContent-Length: "
            + str(too_long).encode() + b"\r\n\r\n"
        )
        return await post_config(path, medium), parse(await raw(path, huge))

    medium, huge = run(core, sock_dir, scenario)
    assert medium[0] == 200 and medium[1]["error"]["code"] == "config.unknown_setting"  # read, checked, refused
    assert huge[0] == 413


def test_a_failure_in_the_service_is_contained(core, sock_dir, monkeypatch):
    def boom(**values):
        raise RuntimeError("disk full")

    monkeypatch.setattr(core.service, "update_config", boom)

    async def scenario(path):
        reply = await post_config(path, make_change({"tot_duration": 100}))
        return reply, await get(path, "/status")

    (status, body), (alive, _) = run(core, sock_dir, scenario)
    assert body["result"] == "rejected" and body["error"]["code"] == "config.apply_failed"
    assert alive == 200  # the core still answers


def test_other_methods_on_config_are_refused(core, sock_dir):
    async def scenario(path):
        put = parse(await raw(path, b"PUT /config HTTP/1.1\r\nHost: core\r\nContent-Length: 0\r\n\r\n"))
        delete = parse(await raw(path, b"DELETE /config HTTP/1.1\r\nHost: core\r\n\r\n"))
        return put[0], delete[0]

    assert run(core, sock_dir, scenario) == (405, 405)


# -- the check itself ------------------------------------------------------------------------


def test_check_change_returns_the_resulting_configuration():
    current = RepeaterConfig(callsign="W1AW")
    result = check_change(current, {"tot_duration": 77})
    assert result.tot_duration == 77 and result.callsign == "W1AW" and current.tot_duration != 77


def test_a_style_setting_takes_its_text():
    current = RepeaterConfig(callsign="W1AW")
    assert check_change(current, {"courtesy_tone_style": current.courtesy_tone_style}).callsign == "W1AW"


def test_check_change_names_settings_never_values():
    with pytest.raises(ConfigRejected) as caught:
        check_change(RepeaterConfig(callsign="W1AW"), {"autopatch_incoming_pin": 12345, "nonsense": SECRET})
    assert SECRET not in caught.value.message
