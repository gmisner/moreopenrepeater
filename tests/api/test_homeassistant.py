import asyncio
import json
import urllib.error
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.homeassistant import COOLDOWN_SECONDS, HomeAssistant, HomeAssistantError
from api.service import RepeaterService
from api.users import UserStore
from controller.macros import Macro
from controller.state_machine import RepeaterConfig

NOW = datetime(2026, 10, 1, 19, 30, tzinfo=timezone.utc)


class FakePost:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def __call__(self, url, body, headers, timeout):
        self.calls.append((url, json.loads(body), headers))
        if self.error:
            raise self.error


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def make(token=None, error=None, **config):
    service = RepeaterService(
        config=RepeaterConfig(callsign="W1AW", homeassistant_url="http://ha.local:8123/", **config),
        macros=[Macro("*7", "porch", command="porch_light", action="homeassistant"), Macro("*8", "alarm", command="event:repeater_alarm", action="homeassistant")],
    )
    post, clock = FakePost(error), Clock()
    ha = HomeAssistant(service, token, post=post, clock=clock, wall_clock=lambda: NOW)
    return service, ha, post, clock


def test_webhook_needs_no_token():
    _service, ha, post, _clock = make()
    ha.send("porch_light", {"x": 1})
    url, body, headers = post.calls[0]
    assert url == "http://ha.local:8123/api/webhook/porch_light"
    assert body == {"x": 1}
    assert "Authorization" not in headers


def test_event_uses_the_token():
    _service, ha, post, _clock = make(token="tok")
    ha.send("event:repeater_alarm", {})
    url, _body, headers = post.calls[0]
    assert url == "http://ha.local:8123/api/events/repeater_alarm"
    assert headers["Authorization"] == "Bearer tok"


def test_errors_read_well():
    _service, ha, _post, _clock = make()
    with pytest.raises(HomeAssistantError, match="access token"):
        ha.send("event:x", {})
    _service, ha, _post, _clock = make(error=urllib.error.HTTPError("u", 401, "no", {}, None), token="bad")
    with pytest.raises(HomeAssistantError, match="answered 401"):
        ha.send("event:x", {})
    _service, ha, _post, _clock = make(error=urllib.error.URLError("refused"))
    with pytest.raises(HomeAssistantError, match="Couldn't reach Home Assistant: refused"):
        ha.send("hook", {})


def test_no_address_set():
    service, ha, _post, _clock = make()
    service.update_config(homeassistant_url="")
    with pytest.raises(HomeAssistantError, match="address"):
        ha.send("hook", {})


def run_dtmf(service, digits):
    async def go():
        for digit in digits:
            service.simulate_dtmf(digit)
        for _ in range(20):
            await asyncio.sleep(0.01)

    asyncio.run(go())


def test_macro_calls_the_webhook_and_says_done():
    service, _ha, post, _clock = make()
    run_dtmf(service, "*7")
    url, body, _headers = post.calls[0]
    assert url.endswith("/api/webhook/porch_light")
    assert body == {"pattern": "*7", "source": "DTMF", "repeater": "W1AW", "time": "2026-10-01T19:30:00+00:00"}
    assert service.controller.queued_announcements[-1] == "tts:Done."


def test_macro_failure_says_failed():
    service, _ha, _post, _clock = make()
    run_dtmf(service, "*8")
    assert service.controller.queued_announcements[-1] == "tts:Failed."


def test_result_can_stay_quiet():
    service, _ha, post, _clock = make(homeassistant_say_result=False)
    run_dtmf(service, "*7")
    assert len(post.calls) == 1
    assert service.controller.queued_announcements == []


def test_cooldown_per_target():
    service, _ha, post, clock = make()
    run_dtmf(service, "*7")
    run_dtmf(service, "*7")
    assert len(post.calls) == 1
    clock.now += COOLDOWN_SECONDS
    run_dtmf(service, "*7")
    assert len(post.calls) == 2


def test_api_test_endpoint_and_macro_validation(tmp_path, monkeypatch):
    monkeypatch.setenv("MOREOPENREPEATER_LOG_PATH", str(tmp_path / "t.log"))
    service = RepeaterService(config=RepeaterConfig(homeassistant_url="http://ha.local:8123"))
    post = FakePost()
    ha = HomeAssistant(service, None, post=post)
    client = TestClient(create_app(service=service, start_background_tick=False, users=UserStore(), homeassistant=ha))

    assert client.get("/api/homeassistant").json() == {"token_set": False}
    assert client.post("/api/homeassistant/test", json={"target": "porch_light"}).json() == {"ok": True}
    assert post.calls[0][1]["test"] is True
    response = client.post("/api/homeassistant/test", json={"target": "event:x"})
    assert response.status_code == 502 and "access token" in response.json()["detail"]
    assert client.post("/api/homeassistant/test", json={"target": "../admin"}).status_code == 422

    ok = {"pattern": "*7", "description": "porch", "command": "porch_light", "action": "homeassistant"}
    assert client.post("/api/macros", json=ok).status_code == 200
    assert client.post("/api/macros", json={**ok, "command": "a/b"}).status_code == 422
    assert client.put("/api/config", json={"homeassistant_url": "ftp://x"}).status_code == 422
