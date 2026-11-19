"""The autopatch names what happens and never logs who was called, who called, or what was keyed (#221, #229).

The dashboard's audit trail records the number and the caller: that is its job. The log is for finding out
what went wrong, and it can be shipped, bundled and read by others, so it holds no phone number (either end), no
access code, no DTMF digit sent into a call, no channel name (it carries the number) and no speech text."""
import asyncio
import logging

from test_autopatch_calls import CALLER, CHANNEL, FakePhone, answer, make, make_incoming, ring_in, wait_until

from api.autopatch import Autopatch
from api.service import RepeaterService
from controller.events import DTMFDigit
from controller.state_machine import RepeaterConfig

NUMBER = "8605551234"


def facts(events):
    """What an event says: its words and its facts (not the times and ids, which are digits too)."""
    return " ".join(f"{e.message} {e.context}" for e in events)


def autopatch_events(events):
    return [e for e in events if e.event.startswith("autopatch.")]


def assert_nothing_private(events, caplog, *secrets):
    said = facts(events) + " " + " ".join(r.getMessage() for r in caplog.records)
    for secret in secrets:
        assert secret not in said, secret


def test_an_outgoing_call_is_a_story_in_three_events_and_none_has_the_number(structured_events, caplog):
    async def scenario():
        service, patch, ami, _ = make()
        phones = []
        ami.on_originate = answer(ami, patch, phones)
        await patch.start()
        assert patch.dial(NUMBER, "DTMF") is None
        await wait_until(lambda: patch.call and patch.call.state == "connected")
        service.handle_audio_events([DTMFDigit(digit="7")])
        phones[0].hang_up()
        await wait_until(lambda: patch.call is None)
        await patch.stop()

    with caplog.at_level(logging.INFO, logger="moreopenrepeater"):
        asyncio.run(scenario())
    assert [e.event for e in autopatch_events(structured_events) if e.event != "autopatch.listening"] == [
        "autopatch.call_started", "autopatch.call_connected", "autopatch.call_ended",
    ]
    started, connected, ended = [e for e in autopatch_events(structured_events) if e.event.startswith("autopatch.call_")]
    assert started.context == {"direction": "outgoing", "via": "DTMF"}
    assert ended.context["result"] == "the other party hung up" and ended.context["direction"] == "outgoing"
    assert ended.context["talk_s"] >= 0
    assert_nothing_private(structured_events, caplog, NUMBER, "860", "5551234", CHANNEL, "8605551234@")


def test_a_dashboard_call_says_so_not_who_made_it(structured_events):
    async def scenario():
        _, patch, ami, _ = make()
        ami.on_originate = answer(ami, patch, [])
        await patch.start()
        patch.dial(NUMBER, "alice")
        await wait_until(lambda: patch.call and patch.call.state == "connected")
        patch.hangup("test over")
        await wait_until(lambda: patch.call is None)
        await patch.stop()

    asyncio.run(scenario())
    [started] = structured_events.named("autopatch.call_started")
    assert started.context == {"direction": "outgoing", "via": "dashboard"}
    assert "alice" not in facts(structured_events)


def test_a_refused_number_is_an_event_with_the_reason_and_without_the_number(structured_events, caplog):
    async def scenario():
        service, patch, ami, _ = make()
        await patch.start()
        patch.dial("19005551234", "DTMF")
        await patch.stop()

    with caplog.at_level(logging.INFO, logger="moreopenrepeater"):
        asyncio.run(scenario())
    [refused] = structured_events.named("autopatch.call_refused")
    assert refused.message == "autopatch call refused: That number is not allowed."
    assert refused.context == {"direction": "outgoing", "via": "DTMF"}
    assert_nothing_private(structured_events, caplog, "19005551234", "900555")


def test_a_call_that_fails_is_an_error_with_the_type_and_not_the_text(structured_events, caplog):
    async def scenario():
        service, patch, ami, _ = make()

        async def unreachable():
            raise OSError(f"cannot reach the trunk for {NUMBER}")

        ami.connect = unreachable
        await patch.start()
        patch.dial(NUMBER, "DTMF")
        await wait_until(lambda: patch.call is None)
        await patch.stop()

    with caplog.at_level(logging.INFO, logger="moreopenrepeater"):
        asyncio.run(scenario())
    [failed] = structured_events.named("autopatch.call_failed")
    assert failed.severity == 40 and failed.error_code == "autopatch.call"
    assert failed.context == {"direction": "outgoing", "exception": "OSError"}
    assert_nothing_private(structured_events, caplog, NUMBER, "5551234")


def test_a_digit_asterisk_will_not_send_is_reported_without_the_digit_or_the_channel(structured_events, caplog):
    async def scenario():
        service, patch, ami, _ = make()
        phones = []
        ami.on_originate = answer(ami, patch, phones)
        original = ami.send_action

        async def refusing(fields):
            response = await original(fields)
            if fields["Action"] == "PlayDTMF":
                return {"Response": "Error", "Message": f"no such channel {fields['Channel']} for digit {fields['Digit']}"}
            return response

        ami.send_action = refusing
        await patch.start()
        patch.dial(NUMBER, "DTMF")
        await wait_until(lambda: patch.call and patch.call.state == "connected")
        service.handle_audio_events([DTMFDigit(digit="#")])
        await wait_until(lambda: structured_events.named("autopatch.dtmf_failed"))
        patch.hangup("test over")
        await wait_until(lambda: patch.call is None)
        await patch.stop()

    with caplog.at_level(logging.INFO, logger="moreopenrepeater"):
        asyncio.run(scenario())
    [failed] = structured_events.named("autopatch.dtmf_failed")
    assert failed.severity == 30 and failed.error_code == "autopatch.asterisk"
    assert_nothing_private(structured_events, caplog, "for digit", "no such channel", CHANNEL)


def test_a_digit_that_could_not_be_sent_at_all_is_reported_without_the_digit_or_the_channel(structured_events, caplog):
    async def scenario():
        service, patch, ami, _ = make()
        ami.on_originate = answer(ami, patch, [])
        original = ami.send_action

        async def dropping(fields):
            if fields["Action"] == "PlayDTMF":
                raise ConnectionError(f"lost the connection sending {fields['Digit']} to {fields['Channel']}")
            return await original(fields)

        ami.send_action = dropping
        await patch.start()
        patch.dial(NUMBER, "DTMF")
        await wait_until(lambda: patch.call and patch.call.state == "connected")
        service.handle_audio_events([DTMFDigit(digit="#")])
        await wait_until(lambda: structured_events.named("autopatch.dtmf_failed"))
        patch.hangup("test over")
        await wait_until(lambda: patch.call is None)
        await patch.stop()

    with caplog.at_level(logging.INFO, logger="moreopenrepeater"):
        asyncio.run(scenario())
    failed = structured_events.named("autopatch.dtmf_failed")[0]
    assert failed.context == {"exception": "ConnectionError"} and failed.error_code == "autopatch.asterisk"
    assert_nothing_private(structured_events, caplog, "sending", "lost the connection", CHANNEL)


def test_speech_that_cannot_be_rendered_is_reported_without_its_text(structured_events, caplog):
    class Exploding:
        sample_rate = 16000

        def render(self, clip, config):
            raise RuntimeError(f"voice failed on {clip}")

    async def scenario():
        service = RepeaterService(config=RepeaterConfig(autopatch_enabled=True), renderer=Exploding())
        patch = Autopatch(service, make()[1].settings, ami_factory=lambda *a: make()[2])
        await patch.start()
        patch.dial(NUMBER, "DTMF")
        await wait_until(lambda: structured_events.named("autopatch.render_failed"))
        patch.hangup("test over")
        await asyncio.sleep(0.05)
        await patch.stop()

    with caplog.at_level(logging.INFO, logger="moreopenrepeater"):
        asyncio.run(scenario())
    failed = structured_events.named("autopatch.render_failed")[0]
    assert failed.error_code == "autopatch.render" and failed.context == {"exception": "RuntimeError"}
    assert_nothing_private(structured_events, caplog, NUMBER, "voice failed", "8 6 0")  # the readback "Dialing 8 6 0..."


def test_an_incoming_call_does_not_record_the_caller_or_the_access_code(structured_events, caplog):
    async def scenario():
        service, patch, ami, audio, audits = make_incoming()
        await patch.start()
        phone = ring_in(ami, patch)
        await phone.connect()
        await asyncio.sleep(0.05)
        phone.press("1234")
        await wait_until(lambda: patch.call is not None)
        phone.hang_up()
        await wait_until(lambda: patch.call is None)
        assert any(CALLER in detail for _, _, detail in audits)  # the audit trail still has who called
        await patch.stop()

    with caplog.at_level(logging.INFO, logger="moreopenrepeater"):
        asyncio.run(scenario())
    names = [e.event for e in autopatch_events(structured_events) if e.event != "autopatch.listening"]
    assert names == ["autopatch.call_started", "autopatch.call_ended"]
    assert structured_events.named("autopatch.call_started")[0].context == {"direction": "incoming"}
    assert_nothing_private(structured_events, caplog, CALLER, CALLER[-4:], "1234")


def test_an_incoming_call_with_the_wrong_code_is_refused_and_the_reason_is_not_the_code(structured_events, caplog):
    async def scenario():
        service, patch, ami, audio, audits = make_incoming()
        await patch.start()
        phone = ring_in(ami, patch)
        await phone.connect()
        for guess in ("0000", "1111", "2222"):
            await asyncio.sleep(0.05)
            phone.press(guess)
        await wait_until(lambda: structured_events.named("autopatch.call_refused"))
        await patch.stop()

    with caplog.at_level(logging.INFO, logger="moreopenrepeater"):
        asyncio.run(scenario())
    [refused] = structured_events.named("autopatch.call_refused")
    assert refused.context == {"direction": "incoming", "reason": "wrong access code"}
    assert_nothing_private(structured_events, caplog, CALLER, "0000", "1111", "2222", "1234")


def test_an_unknown_audiosocket_connection_is_a_warning_with_only_the_call_id(structured_events):
    async def scenario():
        _, patch, _, _ = make()
        await patch.start()
        phone = FakePhone(patch.listen_port, "5a1d6f2e-0000-4000-8000-000000000001")
        await phone.connect()
        await asyncio.wait_for(phone.got_hangup.wait(), 2)
        await patch.stop()

    asyncio.run(scenario())
    [unknown] = structured_events.named("autopatch.unknown_call")
    assert unknown.severity == 30 and unknown.context == {"call_id": "5a1d6f2e-0000-4000-8000-000000000001"}
