"""Macro actions that belong to the API, delivered over the control socket (Stage 3 slice 7c-6b, #229)."""
import asyncio
import logging

import pytest
from control_helpers import make_server
from test_events_stream import next_event

from api import remote_core as remote_module
from api.remote_core import RemoteCore
from api.service import RepeaterService
from controller.config import RepeaterConfig
from controller.events import MailboxCommand, RunAction
from core import control as control_module

PIN = "pin-5566-do-not-log"


@pytest.fixture(autouse=True)
def quick_retries(monkeypatch):
    monkeypatch.setattr(remote_module, "RETRY_DELAYS", (0.05, 0.1))


@pytest.fixture
def spoken(core, monkeypatch):
    said = []
    monkeypatch.setattr(core.service, "speak", lambda clip, on_linked_radio=False: said.append(clip))
    return said


async def until(condition, timeout=5.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not condition():
        assert asyncio.get_running_loop().time() < deadline, "timed out"
        await asyncio.sleep(0.02)


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


async def open_stream(path, handles=""):
    reader, writer = await asyncio.open_unix_connection(str(path))
    query = f"?handles={handles}" if handles else ""
    writer.write(f"GET /events{query} HTTP/1.1\r\nHost: core\r\n\r\n".encode())
    await writer.drain()
    await reader.readuntil(b"\r\n\r\n")
    await next_event(reader)  # the first status
    return reader, writer


def action(name, argument="", pattern="*5"):
    return RunAction(action=name, argument=argument, pattern=pattern)


# -- the core ----------------------------------------------------------------------------------


def test_an_action_goes_to_a_client_that_handles_it(core, sock_dir, spoken):
    async def scenario(path, server):
        reader, writer = await open_stream(path, "metar,sensor")
        core.service.programming.run_action(action("metar", "KPHX"))
        name, data = await next_event(reader)
        writer.close()
        return name, data

    name, data = run(core, sock_dir, scenario)
    assert name == "macro" and data == {"family": "metar", "args": ["KPHX"]}
    assert spoken == []  # somebody took it, so nothing is said about it being unavailable


def test_without_a_client_the_caller_hears_it_is_not_available(core, sock_dir, spoken):
    async def scenario(path, server):
        for name in ("metar", "sensor", "homeassistant", "aprs", "weather"):
            core.service.programming.run_action(action(name, "x"))
        core.service.programming.run_mailbox(MailboxCommand(action="play", box="1"))

    run(core, sock_dir, scenario)
    assert [c.removeprefix("tts:") for c in spoken] == [
        "Airport weather is not set up.", "Site sensors are not set up.", "Home Assistant is not set up.",
        "The A P R S map is not set up.", "Weather is not available.", "The mailbox is not available.",
    ]


def test_a_client_that_handles_something_else_does_not_take_it(core, sock_dir, spoken):
    async def scenario(path, server):
        reader, writer = await open_stream(path, "sensor")
        core.service.programming.run_action(action("metar", "KPHX"))
        writer.close()

    run(core, sock_dir, scenario)
    assert [c.removeprefix("tts:") for c in spoken] == ["Airport weather is not set up."]


def test_every_client_that_handles_it_gets_it_and_unknown_names_are_ignored(core, sock_dir, spoken):
    async def scenario(path, server):
        a = await open_stream(path, "net")
        b = await open_stream(path, "net,nonsense,../etc")
        core.service.programming.run_action(action("net_start"))
        got = [await next_event(a[0]), await next_event(b[0])]
        a[1].close(), b[1].close()
        return got

    got = run(core, sock_dir, scenario)
    assert [g[1]["family"] for g in got] == ["net", "net"] and got[0][1]["args"] == ["net_start", "DTMF"]
    assert spoken == []


def test_a_net_action_with_no_client_is_only_logged(core, sock_dir, spoken, caplog):
    caplog.set_level(logging.WARNING)
    run(core, sock_dir, lambda path, server: _noop(core))
    assert spoken == []  # no spoken complaint, as before: net mode is a quiet failure
    assert "net isn't available (no API connected)" in caplog.text


async def _noop(core):
    core.service.programming.run_action(action("net_start"))


def test_the_arguments_are_never_logged(core, sock_dir, spoken, caplog):
    caplog.set_level(logging.DEBUG)

    async def scenario(path, server):
        reader, writer = await open_stream(path, "mailbox")
        core.service.programming.run_mailbox(MailboxCommand(action="leave", box="2", pin=PIN))
        _, data = await next_event(reader)
        writer.close()
        return data

    data = run(core, sock_dir, scenario)
    assert data["args"][0]["pin"] == PIN  # it crosses the trusted socket...
    ours = " ".join(r.getMessage() for r in caplog.records if r.name.startswith("moreopenrepeater.core"))
    assert PIN not in ours and PIN not in caplog.text  # ...and is not logged


def test_a_client_that_cannot_keep_up_loses_the_newest_and_the_core_carries_on(core, sock_dir, spoken, monkeypatch):
    monkeypatch.setattr(control_module, "MACRO_QUEUE_LIMIT", 2)

    async def scenario(path, server):
        reader, writer = await open_stream(path, "sensor")
        for i in range(20):  # nobody is reading
            core.service.programming.run_action(action("sensor", str(i)))
        await asyncio.sleep(0.1)
        first = await next_event(reader)
        writer.close()
        return first

    run(core, sock_dir, scenario)  # no exception, no stall


def test_when_the_server_stops_macros_fall_back_to_not_available(core, sock_dir, spoken):
    async def scenario(path, server):
        await server.stop()
        core.service.programming.run_action(action("metar", "KPHX"))

    run(core, sock_dir, scenario)
    assert [c.removeprefix("tts:") for c in spoken] == ["Airport weather is not set up."]


# -- RemoteCore ---------------------------------------------------------------------------------


def run_remote(core, sock_dir, scenario, register):
    async def inner(path, server):
        remote = RemoteCore(path)
        register(remote)
        remote.start()
        try:
            await until(lambda: remote.connected and server._macro_clients)
            return await scenario(remote, server)
        finally:
            await remote.stop()

    return run(core, sock_dir, inner)


def test_handlers_are_called_with_the_hook_arguments(core, sock_dir, spoken):
    calls = []

    def register(remote):
        remote.register_macro_handler("metar", lambda airport: calls.append(("metar", airport)))
        remote.register_macro_handler("net", lambda a, who: calls.append(("net", a, who)))
        remote.register_macro_handler("homeassistant", lambda t, s, p: calls.append(("ha", t, s, p)))
        remote.register_macro_handler("mailbox", lambda command: calls.append(("mailbox", command)))

    async def scenario(remote, server):
        programming = core.service.programming
        programming.run_action(action("metar", "KPHX"))
        programming.run_action(action("net_end"))
        programming.run_action(action("homeassistant", "front-door", "*7"))
        programming.run_mailbox(MailboxCommand(action="delete", box="3", pin="9"))
        await until(lambda: len(calls) == 4)

    run_remote(core, sock_dir, scenario, register)
    assert calls == [
        ("metar", "KPHX"), ("net", "net_end", "DTMF"), ("ha", "front-door", "DTMF", "*7"),
        ("mailbox", MailboxCommand(action="delete", box="3", pin="9")),
    ]
    assert spoken == []


def test_the_aprs_handler_returns_the_sentence_and_the_core_speaks_it(core, sock_dir, spoken, monkeypatch):
    def register(remote):
        remote.register_macro_handler("aprs", lambda: "Three stations nearby")

    async def scenario(remote, server):
        sent = []
        monkeypatch.setattr(remote, "speak", lambda clip, on_linked_radio=False: sent.append(clip))
        core.service.programming.run_action(action("aprs"))
        await until(lambda: sent)
        assert sent == ["tts:Three stations nearby"]

    run_remote(core, sock_dir, scenario, register)


def test_the_weather_handler_returns_the_sentence_and_the_core_speaks_it(core, sock_dir, spoken, monkeypatch):
    """The alerts are the API's (it does the polling), so the API words the weather and the core says it."""

    def register(remote):
        remote.register_macro_handler("weather", lambda: "A flood warning is in effect")

    async def scenario(remote, server):
        sent = []
        monkeypatch.setattr(remote, "speak", lambda clip, on_linked_radio=False: sent.append(clip))
        core.service.programming.run_action(action("weather"))
        await until(lambda: sent)
        assert sent == ["tts:A flood warning is in effect"]
        assert spoken == []  # and the core did not word it from its own, empty, weather state

    run_remote(core, sock_dir, scenario, register)


def test_a_handler_that_fails_does_not_end_the_stream(core, sock_dir, spoken, caplog):
    caplog.set_level(logging.WARNING)
    seen = []

    def register(remote):
        def sensor(sensor_id):
            if sensor_id == "bad":
                raise RuntimeError("boom")
            seen.append(sensor_id)

        remote.register_macro_handler("sensor", sensor)

    async def scenario(remote, server):
        core.service.programming.run_action(action("sensor", "bad"))
        core.service.programming.run_action(action("sensor", "good"))
        await until(lambda: seen == ["good"])
        assert remote.connected

    run_remote(core, sock_dir, scenario, register)
    assert "a sensor macro action failed: RuntimeError" in caplog.text


def test_an_async_handler_is_run(core, sock_dir, spoken):
    seen = []

    def register(remote):
        async def metar(airport):
            seen.append(airport)

        remote.register_macro_handler("metar", metar)

    async def scenario(remote, server):
        core.service.programming.run_action(action("metar", "KSDL"))
        await until(lambda: seen == ["KSDL"])

    run_remote(core, sock_dir, scenario, register)


def test_the_core_is_told_only_what_was_registered(core, sock_dir, spoken):
    async def scenario(remote, server):
        (handles, _), = server._macro_clients
        assert handles == frozenset({"metar", "capture"})  # what the test registered, and the RemoteCore's own
        core.service.programming.run_action(action("sensor", "x"))  # not ours: nobody took it
        assert [c.removeprefix("tts:") for c in spoken] == ["Site sensors are not set up."]

    run_remote(core, sock_dir, scenario, lambda remote: remote.register_macro_handler("metar", lambda a: None))


def test_handlers_cannot_be_added_after_start(sock_dir):
    async def go():
        remote = RemoteCore(sock_dir / "x.sock")
        remote.start()
        try:
            with pytest.raises(RuntimeError):
                remote.register_macro_handler("metar", lambda a: None)
        finally:
            await remote.stop()

    asyncio.run(go())


# -- in process -----------------------------------------------------------------------------------


def test_in_process_the_registered_handler_is_the_hook():
    service = RepeaterService(config=RepeaterConfig(callsign="W1AW"), clock=lambda: 0.0)
    calls = []
    service.register_macro_handler("metar", lambda airport: calls.append(airport))
    service.programming.run_action(action("metar", "KPHX"))
    assert calls == ["KPHX"]
    with pytest.raises(ValueError):
        service.register_macro_handler("reboot", lambda: None)


# -- audit (slice 7c-6d) ------------------------------------------------------------------------------


def test_what_a_macro_did_reaches_a_client_that_handles_audit(core, sock_dir, spoken):
    seen = []

    def register(remote):
        remote.register_macro_handler("audit", lambda actor, what, detail: seen.append((actor, what, detail)))

    async def scenario(remote, server):
        core.service.programming.audit_hook("DTMF (alice)", "DTMF (alice) tx_disable", "")
        await until(lambda: seen)

    run_remote(core, sock_dir, scenario, register)
    assert seen == [("DTMF (alice)", "DTMF (alice) tx_disable", "")]
    assert spoken == []  # an audit record is never spoken, handled or not


def test_with_no_client_the_core_logs_who_and_what_but_not_the_detail(core, sock_dir, spoken, caplog):
    caplog.set_level(logging.INFO)

    async def scenario(path, server):
        core.service.programming.audit_hook("DTMF", "DTMF say", "a message that must stay out of the log")

    run(core, sock_dir, scenario)
    assert "audit: DTMF: DTMF say" in caplog.text
    assert "must stay out of the log" not in caplog.text
    assert spoken == []


def test_a_macro_action_is_audited_through_the_core(core, sock_dir, spoken):
    seen = []

    def register(remote):
        remote.register_macro_handler("audit", lambda actor, what, detail: seen.append((actor, what, detail)))

    async def scenario(remote, server):
        core.service.programming.run_action(action("say", "hello", "*8"))
        await until(lambda: seen)

    run_remote(core, sock_dir, scenario, register)
    assert seen == [("DTMF", "DTMF say", "hello")]


def test_the_hook_is_released_when_the_server_stops_and_an_existing_one_is_kept(core, sock_dir, spoken):
    async def scenario(path, server):
        assert core.service.programming.audit_hook is not None
        await server.stop()
        assert core.service.programming.audit_hook is None

    run(core, sock_dir, scenario)

    mine = lambda actor, action, detail: None  # noqa: E731
    core.service.programming.audit_hook = mine

    async def keeps(path, server):
        assert core.service.programming.audit_hook is mine  # not replaced

    run(core, sock_dir, keeps)


def test_in_process_audit_registers_the_same_hook():
    service = RepeaterService(config=RepeaterConfig(callsign="W1AW"), clock=lambda: 0.0)
    records = []
    service.register_macro_handler("audit", lambda actor, what, detail: records.append((actor, what, detail)))
    service.programming.run_action(action("say", "hi"))
    assert records == [("DTMF", "DTMF say", "hi")]
