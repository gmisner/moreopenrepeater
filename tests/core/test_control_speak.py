"""POST /speak on the core's control socket (Stage 3 slice 7b, #229): a module asks the
core to say something, and the core decides. Simulation only: no devices, no real
speech, no Pi."""
import asyncio
import json
import logging
import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from control_helpers import make_server, parse, raw

from contracts import Header, SpeakDecision, SpeakRequest, SpeakStatus, Source, new_id
from core import control as control_module
from playout.renderer import TTS_PREFIX
from playout.tts import TTSError

TEXT = "Severe thunderstorm warning for the valley until 6 PM."


def make_request(text=TEXT, key=None, lifetime=timedelta(minutes=5), emitted=None, **overrides):
    emitted = emitted or datetime.now(timezone.utc)
    values = dict(
        header=Header(
            schema_version=1, message_id=new_id(), emitted_at=emitted, boot_id=new_id(), seq=1,
            source=Source(module="geo", version="0.1.0"), correlation_id=new_id(),
        ),
        idempotency_key=key or f"nws-{new_id()}",
        topic="weather.alert",
        deadline=emitted + lifetime,
        text=text,
    )
    return SpeakRequest(**{**values, **overrides})


async def post(path, body, *, content_type="application/json", extra=b""):
    payload = body if isinstance(body, bytes) else body.model_dump_json().encode()
    request = (
        b"POST /speak HTTP/1.1\r\nHost: core\r\nContent-Type: " + content_type.encode()
        + b"\r\nContent-Length: " + str(len(payload)).encode() + b"\r\n" + extra + b"\r\n" + payload
    )
    return parse(await raw(path, request))


@pytest.fixture
def fakes(core, monkeypatch):
    """No real synthesis, and the announcement queue is observable."""
    state = {"queued": [], "priorities": [], "deferred": [], "render_calls": 0, "queue_ok": True, "render": lambda clip, config: None}

    def render(clip, config):
        state["render_calls"] += 1
        return state["render"](clip, config)

    def queue(clip, on_linked_radio=False, priority=20):
        if not state["queue_ok"]:
            return False
        state["queued"].append(clip)
        state["priorities"].append(priority)
        return True

    monkeypatch.setattr(core.runtime.renderer, "render", render)
    monkeypatch.setattr(core.service, "queue_announcement", queue)
    monkeypatch.setattr(core.service, "defer_announcement", lambda clip, on_linked_radio=False: state["deferred"].append(clip))
    return state


def run(core, sock_dir, scenario, **server_kwargs):
    async def go():
        server = make_server(core, sock_dir / "core.sock", **server_kwargs)
        await server.start()
        try:
            return await scenario(sock_dir / "core.sock", server)
        finally:
            await server.stop()

    return asyncio.run(go())


# --- decisions --------------------------------------------------------------------------

def test_an_accepted_request_is_queued_and_gets_a_transmission_id(core, sock_dir, fakes):
    request = make_request()

    async def scenario(path, server):
        status, body = await post(path, request)
        decision = SpeakDecision.model_validate(body)
        assert status == 200 and decision.status is SpeakStatus.ACCEPTED
        assert decision.transmission_id and decision.header.request_id == request.header.message_id
        assert decision.header.correlation_id == request.header.correlation_id

    run(core, sock_dir, scenario)
    assert fakes["queued"] == [TTS_PREFIX + TEXT]


def test_the_same_key_is_spoken_once(core, sock_dir, fakes):
    first = make_request(key="alert-0001-abcdef")
    retry = make_request(key="alert-0001-abcdef")

    async def scenario(path, server):
        assert SpeakDecision.model_validate((await post(path, first))[1]).status is SpeakStatus.ACCEPTED
        again = SpeakDecision.model_validate((await post(path, retry))[1])
        assert again.status is SpeakStatus.DUPLICATE

    run(core, sock_dir, scenario)
    assert len(fakes["queued"]) == 1 and fakes["render_calls"] == 1


def test_two_identical_requests_at_once_are_spoken_once(core, sock_dir, fakes):
    fakes["render"] = lambda clip, config: time.sleep(0.2)  # a slow render, so they overlap

    async def scenario(path, server):
        results = await asyncio.gather(
            post(path, make_request(key="same-key-1234")), post(path, make_request(key="same-key-1234"))
        )
        return sorted(SpeakDecision.model_validate(b).status.value for _, b in results)

    assert run(core, sock_dir, scenario) == ["accepted", "duplicate"]
    assert len(fakes["queued"]) == 1


def test_an_expired_request_is_dropped_unspoken(core, sock_dir, fakes):
    old = make_request(emitted=datetime.now(timezone.utc) - timedelta(minutes=10), lifetime=timedelta(minutes=5))

    async def scenario(path, server):
        decision = SpeakDecision.model_validate((await post(path, old))[1])
        assert decision.status is SpeakStatus.EXPIRED and decision.error.code == "speak.expired"

    run(core, sock_dir, scenario)
    assert fakes["queued"] == [] and fakes["render_calls"] == 0


def test_a_deadline_that_passes_while_the_speech_is_prepared_is_dropped(core, sock_dir, fakes):
    now = {"t": datetime.now(timezone.utc)}
    request = make_request(emitted=now["t"], lifetime=timedelta(seconds=60))

    def slow(clip, config):
        now["t"] += timedelta(seconds=120)  # the clock moves past the deadline during synthesis

    fakes["render"] = slow

    async def scenario(path, server):
        decision = SpeakDecision.model_validate((await post(path, request))[1])
        assert decision.status is SpeakStatus.EXPIRED

    run(core, sock_dir, scenario, now=lambda: now["t"])
    assert fakes["queued"] == []


def test_a_full_queue_rejects_and_a_retry_is_not_called_a_duplicate(core, sock_dir, fakes):
    fakes["queue_ok"] = False

    async def scenario(path, server):
        first = SpeakDecision.model_validate((await post(path, make_request(key="retry-key-1234")))[1])
        assert first.status is SpeakStatus.REJECTED
        assert first.error.code == "speak.queue_full" and first.error.retryable
        fakes["queue_ok"] = True
        retry = SpeakDecision.model_validate((await post(path, make_request(key="retry-key-1234")))[1])
        assert retry.status is SpeakStatus.ACCEPTED

    run(core, sock_dir, scenario)
    assert len(fakes["queued"]) == 1


def test_a_speech_failure_rejects_and_can_be_retried(core, sock_dir, fakes):
    def broken(clip, config):
        raise TTSError("engine not found")

    fakes["render"] = broken

    async def scenario(path, server):
        decision = SpeakDecision.model_validate((await post(path, make_request(key="tts-key-12345")))[1])
        assert decision.status is SpeakStatus.REJECTED and decision.error.code == "speak.render_failed"
        assert "engine not found" not in json.dumps(decision.model_dump(mode="json"))  # no internals leaked
        fakes["render"] = lambda clip, config: None
        assert SpeakDecision.model_validate((await post(path, make_request(key="tts-key-12345")))[1]).status is SpeakStatus.ACCEPTED

    run(core, sock_dir, scenario)


def test_slow_speech_synthesis_times_out_without_blocking_the_event_loop(core, sock_dir, fakes, monkeypatch):
    monkeypatch.setattr(control_module, "RENDER_DEADLINE_SECONDS", 0.2)
    fakes["render"] = lambda clip, config: time.sleep(1.0)
    ticks = []

    async def scenario(path, server):
        async def watch():  # an event-loop heartbeat: it must keep beating while synthesis runs
            while True:
                ticks.append(time.monotonic())
                await asyncio.sleep(0.02)

        beat = asyncio.create_task(watch())
        decision = SpeakDecision.model_validate((await post(path, make_request()))[1])
        beat.cancel()
        assert decision.error.code == "speak.render_timeout"

    run(core, sock_dir, scenario)
    gaps = [b - a for a, b in zip(ticks, ticks[1:])]
    assert len(ticks) > 5 and max(gaps) < 0.15  # the loop was never held up


def test_a_disabled_transmitter_rejects(core, sock_dir, fakes):
    core.service.update_config(transmitter_enabled=False)

    async def scenario(path, server):
        decision = SpeakDecision.model_validate((await post(path, make_request()))[1])
        assert decision.status is SpeakStatus.REJECTED and decision.error.code == "speak.transmitter_disabled"
        assert not decision.error.retryable

    run(core, sock_dir, scenario)
    assert fakes["queued"] == []


def test_during_a_net_that_holds_announcements_the_speech_is_deferred(core, sock_dir, fakes):
    core.service.set_net_active(True)
    core.service.update_config(net_hold_announcements=True)

    async def scenario(path, server):
        decision = SpeakDecision.model_validate((await post(path, make_request()))[1])
        assert decision.status is SpeakStatus.ACCEPTED

    run(core, sock_dir, scenario)
    assert fakes["deferred"] == [TTS_PREFIX + TEXT] and fakes["queued"] == []


# --- the content is private ------------------------------------------------------------------

def test_the_text_is_never_logged_or_echoed_back(core, sock_dir, fakes, caplog):
    secret = "THE-SECRET-TEXT-XYZ"
    request = make_request(text=secret)

    async def scenario(path, server):
        _, ok = await post(path, request)
        bad = json.loads(request.model_dump_json())
        bad["priority"] = "loud"  # invalid, and the body carries the text
        _, rejected = await post(path, json.dumps(bad).encode())
        return json.dumps(ok), json.dumps(rejected)

    with caplog.at_level(logging.DEBUG):
        ok_text, rejected_text = run(core, sock_dir, scenario)
    assert secret not in ok_text and secret not in rejected_text and secret not in caplog.text


# --- bad requests ----------------------------------------------------------------------------

@pytest.mark.parametrize(
    "body,expected",
    [
        (b"not json", 400),
        (b"{}", 400),
        (b'{"kind": "speak.request"}', 400),
        (b"[]", 400),
    ],
)
def test_an_invalid_body_gets_400_with_locations_and_no_values(core, sock_dir, fakes, body, expected):
    async def scenario(path, server):
        status, answer = await post(path, body)
        assert status == expected and answer["error"] == "not a valid speak.request"
        assert all(set(p) == {"at", "type"} for p in answer["problems"])
        assert (await post(path, make_request()))[0] == 200  # still serving

    run(core, sock_dir, scenario)


def test_the_framing_rules_for_a_post(core, sock_dir, fakes):
    async def scenario(path, server):
        no_length = b"POST /speak HTTP/1.1\r\nContent-Type: application/json\r\n\r\n"
        assert parse(await raw(path, no_length))[0] == 411
        big = b"POST /speak HTTP/1.1\r\nContent-Type: application/json\r\nContent-Length: 99999\r\n\r\n"
        assert parse(await raw(path, big))[0] == 413
        assert (await post(path, b"{}", content_type="text/plain"))[0] == 415
        chunked = b"POST /speak HTTP/1.1\r\nTransfer-Encoding: chunked\r\nContent-Type: application/json\r\n\r\n"
        assert parse(await raw(path, chunked))[0] == 400
        two = b"POST /speak HTTP/1.1\r\nContent-Type: application/json\r\nContent-Length: 2\r\nContent-Length: 2\r\n\r\n{}"
        assert parse(await raw(path, two))[0] == 400
        bad = b"POST /speak HTTP/1.1\r\nContent-Type: application/json\r\nContent-Length: abc\r\n\r\n"
        assert parse(await raw(path, bad))[0] == 400
        short = b"POST /speak HTTP/1.1\r\nContent-Type: application/json\r\nContent-Length: 50\r\n\r\n{}"
        assert parse(await raw(path, short, eof=True))[0] == 400  # the client hung up early
        assert parse(await raw(path, b"GET /speak HTTP/1.1\r\n\r\n"))[0] == 405

    run(core, sock_dir, scenario)


def test_a_client_that_promises_a_body_and_never_sends_it_times_out(core, sock_dir, monkeypatch):
    monkeypatch.setattr(control_module, "REQUEST_DEADLINE_SECONDS", 0.3)

    async def scenario(path, server):
        reader, writer = await asyncio.open_unix_connection(str(path))
        writer.write(b"POST /speak HTTP/1.1\r\nContent-Type: application/json\r\nContent-Length: 100\r\n\r\n{")
        await writer.drain()
        assert parse(await asyncio.wait_for(reader.read(), 5))[0] == 408
        writer.close()

    run(core, sock_dir, scenario)


def test_the_intended_client_can_post(core, sock_dir, fakes):
    async def scenario(path, server):
        transport = httpx.AsyncHTTPTransport(uds=str(path))
        async with httpx.AsyncClient(transport=transport, base_url="http://core") as client:
            response = await client.post("/speak", content=make_request().model_dump_json(), headers={"Content-Type": "application/json"})
            assert response.status_code == 200
            assert SpeakDecision.model_validate(response.json()).status is SpeakStatus.ACCEPTED

    run(core, sock_dir, scenario)


# --- priority ------------------------------------------------------------------------------------

@pytest.mark.parametrize("name,value", [("low", 10), ("normal", 20), ("high", 30), ("emergency", 40)])
def test_the_priority_reaches_the_core_queue(core, sock_dir, fakes, name, value):
    async def scenario(path, server):
        assert (await post(path, make_request(priority=value)))[0] == 200

    run(core, sock_dir, scenario)
    assert fakes["priorities"] == [value]


def test_an_emergency_is_not_held_back_by_a_net_but_a_high_priority_one_is(core, sock_dir, fakes):
    core.service.set_net_active(True)
    core.service.update_config(net_hold_announcements=True)

    async def scenario(path, server):
        await post(path, make_request(priority=30))  # high: held until the net ends
        await post(path, make_request(priority=40))  # emergency: queued now, to play at the next idle

    run(core, sock_dir, scenario)
    assert len(fakes["deferred"]) == 1 and len(fakes["queued"]) == 1
    assert fakes["priorities"] == [40]


def test_an_emergency_is_still_refused_when_the_transmitter_is_disabled(core, sock_dir, fakes):
    core.service.update_config(transmitter_enabled=False)

    async def scenario(path, server):
        decision = SpeakDecision.model_validate((await post(path, make_request(priority=40)))[1])
        assert decision.status is SpeakStatus.REJECTED and decision.error.code == "speak.transmitter_disabled"

    run(core, sock_dir, scenario)


def test_priority_orders_real_requests_in_the_real_queue(core, sock_dir, monkeypatch):
    """No fakes for the queue: requests go through the real controller queue."""
    monkeypatch.setattr(core.runtime.renderer, "render", lambda clip, config: None)

    async def scenario(path, server):
        for text, priority in [("routine", 20), ("alert", 40), ("minor", 10)]:
            assert (await post(path, make_request(text=text, priority=priority)))[0] == 200

    run(core, sock_dir, scenario)
    assert [clip.removeprefix(TTS_PREFIX) for clip in core.service.controller.queued_announcements] == [
        "alert", "routine", "minor"
    ]
