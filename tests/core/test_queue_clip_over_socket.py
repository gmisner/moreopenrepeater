"""Playing a clip through the core over the control socket (Stage 3 slice 7c-4c, #229).
Simulation only: no real speech, no devices."""
import asyncio
import logging

import pytest
from control_helpers import make_server
from pydantic import ValidationError
from test_control_command import make_command, post_command

from api import remote_core as remote_module
from api.core_runtime import AnnouncementQueueFull
from api.remote_core import RemoteCore
from api.service.core_handle import CommandRefused, CoreUnavailable
from contracts import CoreCommand
from core import control as control_module
from playout.renderer import UnknownClipError
from playout.tts import TTSError

TEXT = "Net control is W1AW, secret-phrase-do-not-log"


@pytest.fixture(autouse=True)
def quick_retries(monkeypatch):
    monkeypatch.setattr(remote_module, "RETRY_DELAYS", (0.05, 0.1))


@pytest.fixture
def fakes(core, monkeypatch):
    """No real synthesis, and the announcement queue is observable."""
    state = {"rendered": [], "queued": [], "queue_ok": True, "render": lambda clip, config: None}

    def render(clip, config):
        state["rendered"].append(clip)
        return state["render"](clip, config)

    def queue(clip, on_linked_radio=False, priority=20):
        if not state["queue_ok"]:
            return False
        state["queued"].append((clip, on_linked_radio))
        return True

    monkeypatch.setattr(core.runtime.renderer, "render", render)
    monkeypatch.setattr(core.service, "queue_announcement", queue)
    return state


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
            await until(lambda: remote.connected)
            return await scenario(remote, server)
        finally:
            await remote.stop()

    return run(core, sock_dir, inner, **kwargs)


# -- the contract --------------------------------------------------------------------------


@pytest.mark.parametrize("clip", ["", "x" * 2101, "bad\x00clip", 5, None])
def test_a_bad_clip_is_refused_by_the_contract(clip):
    with pytest.raises(ValidationError):
        CoreCommand(header=make_command("clear_lockout").header, command="queue_clip", params={"clip": clip, "on_linked_radio": False})


def test_the_flag_must_be_a_bool():
    with pytest.raises(ValidationError):
        CoreCommand(header=make_command("clear_lockout").header, command="queue_clip", params={"clip": "id", "on_linked_radio": "yes"})


# -- the endpoint --------------------------------------------------------------------------


def test_a_clip_is_rendered_then_queued(core, sock_dir, fakes):
    async def scenario(path, server):
        a = await post_command(path, make_command("queue_clip", clip="id", on_linked_radio=False))
        b = await post_command(path, make_command("queue_clip", clip="tts:" + TEXT, on_linked_radio=True))
        return a[1], b[1]

    a, b = run(core, sock_dir, scenario)
    assert a["status"] == "applied" and b["status"] == "applied"
    assert fakes["rendered"] == ["id", "tts:" + TEXT]  # rendered first, so the controller knows the length
    assert fakes["queued"] == [("id", False), ("tts:" + TEXT, True)]


def test_it_needs_no_simulator_flag(core, sock_dir, fakes):
    async def scenario(path, server):
        return (await post_command(path, make_command("queue_clip", clip="id", on_linked_radio=False)))[1]["status"]

    assert run(core, sock_dir, scenario, allow_simulate=False) == "applied"


@pytest.mark.parametrize(
    "error, code, retryable",
    [
        (UnknownClipError("nope"), "command.unknown_clip", False),
        (TTSError("engine down"), "command.render_failed", True),
    ],
)
def test_a_render_failure_is_refused_with_its_reason(core, sock_dir, fakes, error, code, retryable):
    def fail(clip, config):
        raise error

    fakes["render"] = fail

    async def scenario(path, server):
        return (await post_command(path, make_command("queue_clip", clip="tts:x", on_linked_radio=False)))[1]

    body = run(core, sock_dir, scenario)
    assert body["status"] == "rejected" and body["error"]["code"] == code and body["error"]["retryable"] is retryable
    assert fakes["queued"] == []


def test_a_full_queue_is_refused_as_retryable(core, sock_dir, fakes):
    fakes["queue_ok"] = False

    async def scenario(path, server):
        return (await post_command(path, make_command("queue_clip", clip="id", on_linked_radio=False)))[1]

    body = run(core, sock_dir, scenario)
    assert body["error"]["code"] == "command.queue_full" and body["error"]["retryable"]


def test_slow_synthesis_times_out_and_the_core_keeps_answering(core, sock_dir, fakes, monkeypatch):
    import time

    monkeypatch.setattr(control_module, "RENDER_DEADLINE_SECONDS", 0.2)
    fakes["render"] = lambda clip, config: time.sleep(0.6)

    async def scenario(path, server):
        slow = (await post_command(path, make_command("queue_clip", clip="tts:x", on_linked_radio=False)))[1]
        alive = (await post_command(path, make_command("set_net_active", active=False)))[1]
        return slow, alive

    slow, alive = run(core, sock_dir, scenario)
    assert slow["error"]["code"] == "command.render_timeout" and alive["status"] == "applied"
    assert fakes["queued"] == []


def test_the_clip_is_never_logged(core, sock_dir, fakes, caplog):
    caplog.set_level(logging.DEBUG)

    async def scenario(path, server):
        await post_command(path, make_command("queue_clip", clip="tts:" + TEXT, on_linked_radio=False))

    run(core, sock_dir, scenario)
    ours = " ".join(r.getMessage() for r in caplog.records if r.name == "moreopenrepeater.core.control")
    assert "queue_clip applied" in ours and "secret-phrase" not in ours


# -- RemoteCore --------------------------------------------------------------------------------


def test_render_and_queue_raises_the_errors_the_in_process_one_does(core, sock_dir, fakes):
    async def scenario(remote, server):
        await remote.render_and_queue("id")
        assert fakes["queued"] == [("id", False)]
        fakes["render"] = lambda clip, config: (_ for _ in ()).throw(UnknownClipError("x"))
        with pytest.raises(UnknownClipError):
            await remote.render_and_queue("asset:missing")
        fakes["render"] = lambda clip, config: (_ for _ in ()).throw(TTSError("x"))
        with pytest.raises(TTSError):
            await remote.render_and_queue("tts:hi")
        fakes["render"] = lambda clip, config: None
        fakes["queue_ok"] = False
        with pytest.raises(AnnouncementQueueFull):
            await remote.render_and_queue("id")

    run_remote(core, sock_dir, scenario)


def test_render_and_queue_to_a_core_that_is_down_raises_unavailable(core, sock_dir, fakes):
    async def scenario(remote, server):
        await server.stop()
        with pytest.raises(CoreUnavailable):
            await remote.render_and_queue("id")

    run_remote(core, sock_dir, scenario)


def test_speak_queues_without_waiting(core, sock_dir, fakes):
    async def scenario(remote, server):
        remote.speak("tts:hello", on_linked_radio=True)  # returns at once
        await until(lambda: fakes["queued"] == [("tts:hello", True)])

    run_remote(core, sock_dir, scenario)


def test_a_failed_speak_is_logged_without_the_clip_and_does_not_raise(core, sock_dir, fakes, caplog):
    caplog.set_level(logging.DEBUG)
    fakes["render"] = lambda clip, config: (_ for _ in ()).throw(TTSError("x"))

    async def scenario(remote, server):
        remote.speak("tts:" + TEXT)
        await until(lambda: "could not queue a spoken clip" in caplog.text)
        await until(lambda: not remote._speaking)

    run_remote(core, sock_dir, scenario)
    assert "secret-phrase" not in caplog.text


def test_speak_without_a_running_loop_drops_the_clip_quietly(sock_dir, caplog):
    caplog.set_level(logging.WARNING)
    RemoteCore(sock_dir / "x.sock").speak("tts:hello")
    assert "needs a running event loop" in caplog.text


def test_speak_to_a_core_that_is_down_does_not_raise(core, sock_dir, fakes):
    async def scenario(remote, server):
        await server.stop()
        remote.speak("id")
        await until(lambda: not remote._speaking)

    run_remote(core, sock_dir, scenario)


def test_an_other_refusal_passes_through(core, sock_dir, fakes):
    async def scenario(remote, server):
        server._runtime.render_and_queue = None  # something unexpected inside the core
        with pytest.raises(CommandRefused) as caught:
            await remote.render_and_queue("id")
        assert caught.value.code == "command.apply_failed"

    run_remote(core, sock_dir, scenario)
