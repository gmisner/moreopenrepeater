import json
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from api.app import create_app
from api.auth import AuthSettings
from api.log_hub import LogHub, LogShipper
from api.logging_config import configure_logging
from api.persistence import StateStore
from api.service import RepeaterService


def make_client(hub=None, shipper=None, posts=None):
    tmp = Path(tempfile.mkdtemp())
    hub = hub or LogHub()
    if shipper is None:
        shipper = LogShipper(hub, store=StateStore(tmp / "ship.json"), post=posts or (lambda *a, **k: None))
    app = create_app(
        service=RepeaterService(),
        start_background_tick=False,
        log_path=tmp / "t.log",
        log_hub=hub,
        log_shipper=shipper,
        auth_settings=AuthSettings("admin", "adminadmin"),
    )
    return TestClient(app), hub, shipper, tmp


def test_hub_assigns_monotonic_sequences():
    hub = LogHub()
    first = hub.publish("one", "INFO")
    second = hub.publish("two", "WARNING")
    assert first.seq == 1 and second.seq == 2
    lines, cursor = hub.since(0)
    assert [e.line for e in lines] == ["one", "two"]
    assert cursor == 2
    assert hub.since(1)[0][0].line == "two"


def test_configure_logging_pushes_into_the_hub(tmp_path):
    hub = LogHub()
    logger = configure_logging(tmp_path / "x.log", hub=hub)
    logger.info("hello from hub")
    lines, _ = hub.since(0)
    assert any("hello from hub" in entry.line for entry in lines)


def test_logs_since_and_stream_require_auth():
    client, hub, _, _ = make_client()
    assert client.get("/api/logs/since").status_code == 401
    assert client.get("/api/logs/stream").status_code == 401


def test_logs_since_returns_hub_lines():
    client, hub, _, _ = make_client()
    start = hub.cursor
    first = hub.publish("alpha", "INFO")
    second = hub.publish("beta", "ERROR")

    response = client.get("/api/logs/since", params={"after": start}, auth=("admin", "adminadmin"))

    assert response.status_code == 200
    body = response.json()
    assert [row["line"] for row in body["lines"]] == ["alpha", "beta"]
    assert body["after"] == second.seq

    only_new = client.get(
        "/api/logs/since", params={"after": first.seq}, auth=("admin", "adminadmin")
    ).json()
    assert [row["line"] for row in only_new["lines"]] == ["beta"]


def test_log_stream_route_is_registered():
    client, _, _, _ = make_client()
    paths = {route.path for route in client.app.routes}
    assert "/api/logs/stream" in paths


def test_hub_subscribers_receive_live_lines():
    hub = LogHub()
    subscriber = hub.subscribe()
    hub.publish("live-line", "WARNING")
    entry = subscriber.get(timeout=1.0)
    assert entry is not None and entry.line == "live-line"
    hub.unsubscribe(subscriber)


def test_log_shipping_settings_hide_secrets_and_test_post():
    posted = []

    def capture(url, token, callsign, lines, timeout):
        posted.append({"url": url, "token": token, "callsign": callsign, "lines": list(lines), "timeout": timeout})

    client, hub, shipper, tmp = make_client(posts=capture)

    empty = client.get("/api/log-shipping", auth=("admin", "adminadmin")).json()
    assert empty["settings"]["enabled"] is False
    assert empty["secrets_set"] == {"token": False, "url": False}
    assert "url" not in empty["settings"]

    saved = client.put(
        "/api/log-shipping",
        auth=("admin", "adminadmin"),
        json={
            "enabled": True,
            "url": "https://helper.example/ingest",
            "token": "secret",
            "min_level": "WARNING",
            "batch_seconds": 1,
        },
    ).json()
    assert saved["active"] is True
    assert saved["secrets_set"] == {"token": True, "url": True}
    assert saved["settings"]["min_level"] == "WARNING"
    assert "token" not in client.get("/api/log-shipping", auth=("admin", "adminadmin")).json()["settings"]

    test = client.post("/api/log-shipping/test", auth=("admin", "adminadmin")).json()
    assert test["error"] is None
    assert posted[-1]["url"] == "https://helper.example/ingest"
    assert posted[-1]["token"] == "secret"
    assert posted[-1]["lines"][0].line == "moreopenrepeater remote logging test"

    # Persists across a fresh shipper on the same file.
    again = LogShipper(hub, store=StateStore(tmp / "ship.json"), post=capture)
    assert again.settings.enabled is True
    assert again.settings.url == "https://helper.example/ingest"
    assert again.settings.token == "secret"
    shipper.stop()


def test_post_batch_sends_an_axiom_style_event_array():
    from api.log_hub import LogLine, _post_batch

    captured = {}

    class FakeResponse:
        def read(self):
            return b"{}"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(request, timeout=0):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data.decode())
        captured["auth"] = request.headers.get("Authorization")
        return FakeResponse()

    import api.log_hub as hub_mod

    original = hub_mod.urllib.request.urlopen
    hub_mod.urllib.request.urlopen = fake_urlopen
    try:
        _post_batch(
            "https://api.axiom.co/v1/ingest/repeater",
            "xapt-test",
            "W1AW",
            [LogLine(seq=3, line="usb gone", level="WARNING", at=1_700_000_000.0)],
            5.0,
        )
    finally:
        hub_mod.urllib.request.urlopen = original

    assert captured["url"] == "https://api.axiom.co/v1/ingest/repeater"
    assert captured["auth"] == "Bearer xapt-test"
    assert captured["body"] == [
        {
            "_time": 1_700_000_000.0,
            "seq": 3,
            "level": "WARNING",
            "message": "usb gone",
            "callsign": "W1AW",
            "source": "moreopenrepeater",
        }
    ]
