"""The API's background loops and services name what they do (#221, #229): weather, APRS, the AllStar link."""
import asyncio
import logging

import pytest
from fastapi import FastAPI
from test_weather_routes import LOCATED, FakeNWS, make_client

from api import runtime as runtime_module
from api.runtime import RuntimeBuilder, _loop_failed
from controller.events import DialPatch
from playout.tts import TTSError

SECRET = "private-detail-do-not-log"


def everything(events):
    return " ".join(e.model_dump_json() for e in events)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    from api import core_runtime

    monkeypatch.setattr(core_runtime, "kokoro_service_for", lambda renderer: None)
    monkeypatch.setattr(core_runtime, "piper_service_for", lambda renderer, voice="": None)
    monkeypatch.setattr(core_runtime, "sd_notify", lambda *a, **k: True, raising=False)
    for name in ("APRS_DISABLED_POLL_SECONDS", "WEATHER_DISABLED_POLL_SECONDS", "ANNOUNCEMENT_POLL_SECONDS"):
        monkeypatch.setattr(runtime_module, name, 0.02)


def make_runtime(**overrides):
    built = RuntimeBuilder(start_background_tick=True, **overrides).build()
    built.attach_agent(FastAPI())
    return built


@pytest.fixture
def runtime():
    return make_runtime()


def serve(runtime, seconds=0.4):
    async def go():
        async with runtime.lifespan(None):
            await asyncio.sleep(seconds)

    asyncio.run(go())


class FakeAprs:
    fail = False

    def __init__(self, *args, **kwargs):
        pass

    async def connect(self):
        if self.fail:
            raise OSError(SECRET)

    async def send_packet(self, packet):
        pass

    async def close(self):
        pass


def test_a_beacon_that_goes_out_is_an_event_with_where_it_went(runtime, monkeypatch, structured_events):
    monkeypatch.setattr(runtime_module, "APRSClient", FakeAprs)
    runtime.service.update_config(aprs_enabled=True, aprs_callsign="W1AW", aprs_beacon_interval=60, aprs_server="aprs.example.net", aprs_port=14580)
    serve(runtime)
    [sent] = structured_events.named("aprs.beacon_sent")
    assert sent.context == {"server": "aprs.example.net", "port": 14580} and sent.severity == 20


def test_a_beacon_that_fails_is_an_error_with_the_type_and_not_the_text(runtime, monkeypatch, structured_events):
    class Failing(FakeAprs):
        fail = True

    monkeypatch.setattr(runtime_module, "APRSClient", Failing)
    runtime.service.update_config(aprs_enabled=True, aprs_callsign="W1AW", aprs_beacon_interval=60)
    serve(runtime)
    failed = structured_events.named("aprs.beacon_failed")[0]
    assert failed.severity == 40 and failed.error_code == "aprs.beacon" and failed.context["exception"] == "OSError"
    assert SECRET not in everything(structured_events)
    assert structured_events.named("aprs.beacon_sent") == []


def due_weather(runtime, monkeypatch, text="Clear skies."):
    monkeypatch.setattr(runtime.service, "weather_schedule_due", lambda: True)
    monkeypatch.setattr(runtime.service, "scheduled_weather_text", lambda: text)


def test_a_scheduled_weather_announcement_that_is_due_is_an_event(runtime, monkeypatch, structured_events):
    due_weather(runtime, monkeypatch)

    async def queued(self, clip, on_linked_radio=False):
        pass

    monkeypatch.setattr(runtime_module.CoreRuntime, "render_and_queue", queued)
    serve(runtime)
    assert structured_events.named("weather.scheduled_due") and not structured_events.named("weather.announce_failed")


def test_a_scheduled_announcement_that_cannot_be_played_says_so(runtime, monkeypatch, structured_events):
    due_weather(runtime, monkeypatch)

    async def broken(self, clip, on_linked_radio=False):
        raise TTSError(SECRET)

    monkeypatch.setattr(runtime_module.CoreRuntime, "render_and_queue", broken)
    serve(runtime)
    failed = structured_events.named("weather.announce_failed")[0]
    assert failed.severity == 40 and failed.error_code == "weather.announce"
    assert failed.context == {"kind": "scheduled", "exception": "TTSError"}
    assert SECRET not in everything(structured_events)


def test_an_unexpected_error_in_the_weather_schedule_is_a_loop_failure_not_a_crash(runtime, monkeypatch, structured_events):
    def broken():
        raise RuntimeError(SECRET)

    monkeypatch.setattr(runtime.service, "weather_schedule_due", lambda: True)
    monkeypatch.setattr(runtime.service, "scheduled_weather_text", broken)
    serve(runtime)
    failed = structured_events.named("weather.loop_failed")[0]
    assert failed.error_code == "weather.unexpected" and failed.context == {"loop": "schedule", "exception": "RuntimeError"}
    assert len(structured_events.named("weather.loop_failed")) > 1  # and the loop kept going
    assert SECRET not in everything(structured_events)


def test_an_alert_check_that_cannot_reach_the_weather_service_is_a_warning(structured_events):
    def unreachable(lat, lon, contact=""):
        raise OSError(SECRET)

    runtime = make_runtime(fetch_weather=unreachable)
    runtime.service.update_config(wx_alerts_enabled=True, wx_lat=35.2, wx_lon=-101.8, wx_poll_interval=60)
    serve(runtime)
    failed = structured_events.named("weather.check_failed")[0]
    assert failed.severity == 30 and failed.error_code == "weather.fetch" and failed.context == {"exception": "OSError"}
    assert SECRET in failed.message  # the words are kept, as before: a network error says what could not be reached


def test_an_announced_alert_names_which_alert(structured_events):
    client, service, _ = make_client(wx_alerts_enabled=True, wx_min_severity="Severe", **LOCATED)
    client.post("/api/weather/check")
    announced = structured_events.named("weather.alert_announced")
    assert [e.context["alert"] for e in announced] == ["Flood Watch", "Flood Watch"]
    assert all(e.context["alert_id"] for e in announced)
    client.post("/api/weather/check")  # not repeated
    assert len(structured_events.named("weather.alert_announced")) == 2


def test_the_autopatch_request_over_dtmf_is_an_event_without_the_number(structured_events):
    client, service, _ = make_client()
    service.patch.run_command(DialPatch(number="5551234567"))
    [requested] = structured_events.named("autopatch.dial_requested")
    assert requested.context == {} and "5551234567" not in requested.model_dump_json()  # the request is not the call


@pytest.mark.parametrize("area", ["mailbox", "transcripts", "net", "updates", "alerts", "sensors"])
def test_every_background_loop_reports_its_failures_the_same_way(area, structured_events, caplog):
    with caplog.at_level(logging.ERROR):
        try:
            raise ValueError(SECRET)
        except ValueError:
            _loop_failed(area, "in a test")
    [failed] = structured_events.named(f"{area}.loop_failed")
    assert failed.severity == 40 and failed.error_code == f"{area}.unexpected" and failed.context == {"exception": "ValueError"}
    assert failed.message == "unexpected error in a test"
    assert SECRET not in failed.model_dump_json()
    assert caplog.records[0].exc_info is not None  # the traceback is still in the text log


def test_an_alert_that_cannot_be_announced_is_an_error_that_does_not_carry_the_alert_text(monkeypatch, structured_events):
    async def broken(self, clip, on_linked_radio=False):
        raise TTSError(SECRET)

    monkeypatch.setattr(runtime_module.CoreRuntime, "render_and_queue", broken)
    runtime = make_runtime(fetch_weather=FakeNWS())
    runtime.service.update_config(wx_alerts_enabled=True, wx_lat=35.2, wx_lon=-101.8, wx_min_severity="Severe", wx_poll_interval=60)
    serve(runtime)
    failed = structured_events.named("weather.announce_failed")[0]
    assert failed.context == {"kind": "alert", "exception": "TTSError"} and failed.message == "couldn't announce a weather alert"
    assert "Flood" not in failed.model_dump_json() and SECRET not in failed.model_dump_json()  # the clip is the alert's text


AMI = runtime_module.LinkSettings("10.1.2.3", 5038, "admin", "ami-password-do-not-log", "41520")


class FakeLinkClient:
    fail = False

    def __init__(self, *args, **kwargs):
        pass

    async def connect(self):
        if self.fail:
            raise ConnectionError(SECRET)

    async def events(self):
        return
        yield  # an empty stream: the connection ends at once

    async def close(self):
        pass


def test_the_allstar_link_connecting_is_an_event_without_the_password(monkeypatch, structured_events):
    monkeypatch.setattr(runtime_module, "NodeLinkClient", FakeLinkClient)
    monkeypatch.setattr(runtime_module, "LINK_RECONNECT_DELAY_SECONDS", 0.05)
    serve(make_runtime(link_settings=AMI))
    connected = structured_events.named("link.connected")[0]
    assert connected.context == {"host": "10.1.2.3", "port": 5038, "node": "41520"} and connected.severity == 20
    assert "ami-password-do-not-log" not in everything(structured_events)


def test_the_allstar_link_failing_is_an_error_and_says_when_it_will_try_again(monkeypatch, structured_events):
    class Failing(FakeLinkClient):
        fail = True

    monkeypatch.setattr(runtime_module, "NodeLinkClient", Failing)
    monkeypatch.setattr(runtime_module, "LINK_RECONNECT_DELAY_SECONDS", 0.05)
    serve(make_runtime(link_settings=AMI))
    lost = structured_events.named("link.connection_lost")[0]
    assert lost.severity == 40 and lost.error_code == "link.ami_connection"
    assert lost.context == {"retry_s": 0.05, "exception": "ConnectionError"}
    assert structured_events.named("link.connected") == []
    assert SECRET not in everything(structured_events)
