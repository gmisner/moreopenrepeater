import asyncio
import json
import os
import stat
import tempfile
import urllib.error
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api import notify
from api.app import create_app
from api.assets import AudioAssetStore
from api.audit import AuditLog
from api.notify import Alert, NotificationSettings, Notifier, describe_error
from api.persistence import StateStore
from api.service import RepeaterService
from controller.state_machine import RepeaterConfig


class FakeSenders(dict):
    def __init__(self, failing=()):
        self.calls = []
        for name in ("ntfy", "telegram", "email", "webhook"):
            self[name] = self._sender(name, name in failing)

    def _sender(self, name, fail):
        def send(settings, alert, title, timeout):
            self.calls.append((name, title, alert.message))
            if fail:
                raise urllib.error.URLError("connection refused")

        return send


def make_notifier(senders=None, **settings):
    clock = {"now": 1000.0}
    notifier = Notifier(senders=senders or FakeSenders(), clock=lambda: clock["now"], station=lambda: "W1AW")
    notifier.update(**{"enabled": True, "ntfy_url": "https://ntfy.sh/topic", **settings})
    return notifier, clock


def run(coroutine):
    return asyncio.run(coroutine)


def test_sends_to_every_configured_channel_with_the_station_in_the_title():
    senders = FakeSenders()
    notifier, _ = make_notifier(senders, webhook_url="https://example.com/hook")

    run(notifier.notify(Alert("lockout", "Locked out", "details")))

    assert sorted(c[0] for c in senders.calls) == ["ntfy", "webhook"]
    assert senders.calls[0][1] == "W1AW: Locked out"
    assert notifier.recent[0]["sent"] == ["ntfy", "webhook"]


def test_channels_need_all_their_settings():
    settings = NotificationSettings(telegram_token="1:abc", smtp_host="smtp.example.com")
    assert notify.configured_channels(settings) == []
    settings = NotificationSettings(telegram_token="1:abc", telegram_chat_id="42", smtp_host="smtp.example.com", email_to="me@example.com")
    assert notify.configured_channels(settings) == ["telegram", "email"]


def test_the_same_alert_is_not_repeated_soon_but_its_end_is():
    senders = FakeSenders()
    notifier, clock = make_notifier(senders)

    run(notifier.notify(Alert("lockout", "Locked out", "")))
    clock["now"] += 60
    run(notifier.notify(Alert("lockout", "Lockout cleared", "")))
    clock["now"] += 60
    run(notifier.notify(Alert("lockout", "Locked out", "")))

    assert [c[1] for c in senders.calls] == ["W1AW: Locked out", "W1AW: Lockout cleared"]
    assert notifier.recent[0]["note"] == "not sent again so soon"
    clock["now"] += notify.REPEAT_AFTER_SECONDS
    run(notifier.notify(Alert("lockout", "Locked out", "")))
    assert len(senders.calls) == 3


def test_hourly_cap():
    senders = FakeSenders()
    notifier, clock = make_notifier(senders)

    for i in range(notify.MAX_PER_HOUR + 3):
        run(notifier.notify(Alert(f"k{i}", f"alert {i}", "")))
    assert len(senders.calls) == notify.MAX_PER_HOUR

    clock["now"] += 3601
    run(notifier.notify(Alert("later", "later", "")))
    assert len(senders.calls) == notify.MAX_PER_HOUR + 1


def test_nothing_is_sent_while_alerts_are_off_but_it_is_listed():
    senders = FakeSenders()
    notifier, _ = make_notifier(senders, enabled=False)

    run(notifier.notify(Alert("x", "Something", "")))

    assert senders.calls == []
    assert notifier.recent[0]["note"] == "alerts are off"


def test_a_failing_channel_doesnt_stop_the_others_and_is_audited():
    senders = FakeSenders(failing={"ntfy"})
    notifier, _ = make_notifier(senders, webhook_url="https://example.com/hook")
    audited = []
    notifier.audit_hook = lambda at, action, detail: audited.append((action, detail))

    run(notifier.notify(Alert("x", "Something", "")))

    assert notifier.recent[0]["sent"] == ["webhook"]
    assert notifier.recent[0]["errors"] == {"ntfy": "connection refused"}
    assert audited == [("alert sent", "Something → webhook; failed: ntfy (connection refused)")]


def test_test_alert_ignores_the_switch_and_the_rate_limit():
    senders = FakeSenders(failing={"webhook"})
    notifier, _ = make_notifier(senders, enabled=False, webhook_url="https://example.com/hook")

    results = run(notifier.send_test())
    results_again = run(notifier.send_test())

    assert results == results_again == {"ntfy": None, "webhook": "connection refused"}


def test_settings_live_in_an_owner_only_file(tmp_path):
    path = tmp_path / "alerts.json"
    notifier = Notifier(StateStore(path))
    notifier.update(enabled=True, smtp_password="hunter22")

    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert Notifier(StateStore(path)).settings.smtp_password == "hunter22"


def test_errors_never_include_the_url():
    error = urllib.error.HTTPError("https://api.telegram.org/bot123:SECRET/sendMessage", 401, "Unauthorized", {}, None)
    assert describe_error(error) == "HTTP 401 Unauthorized"


def test_connection_errors_read_plainly():
    refused = urllib.error.URLError(ConnectionRefusedError(61, "Connection refused"))
    assert describe_error(refused) == "Connection refused"
    assert describe_error(urllib.error.URLError("no host given")) == "no host given"
    assert describe_error(TimeoutError()) == "TimeoutError"


def capture_posts(monkeypatch):
    posts = []
    monkeypatch.setattr(notify, "_post", lambda url, body, headers, timeout: posts.append((url, body, headers)))
    return posts


def test_ntfy_request(monkeypatch):
    posts = capture_posts(monkeypatch)
    settings = NotificationSettings(ntfy_url="https://ntfy.sh/w1aw-alerts", ntfy_token="tk_abc")

    notify.send_ntfy(settings, Alert("x", "", "It broke", "critical"), "W1AW: CPU temperature 85 °C", 5)

    url, body, headers = posts[0]
    assert url == "https://ntfy.sh/w1aw-alerts" and body == b"It broke"
    assert headers["Priority"] == "urgent" and headers["Authorization"] == "Bearer tk_abc"
    assert headers["Title"] == "W1AW: CPU temperature 85 °C"  # ° is Latin-1
    headers["Title"].encode("latin-1")


def test_ntfy_encodes_titles_beyond_latin1(monkeypatch):
    posts = capture_posts(monkeypatch)
    notify.send_ntfy(NotificationSettings(ntfy_url="https://ntfy.sh/t"), Alert("x", "", "m"), "Pi — hot", 5)
    assert posts[0][2]["Title"].startswith("=?UTF-8?B?")


def test_telegram_and_webhook_requests(monkeypatch):
    posts = capture_posts(monkeypatch)
    settings = NotificationSettings(telegram_token="123:abc", telegram_chat_id="-100", webhook_url="https://hooks.example.com/x")
    alert = Alert("lockout", "Locked out", "details", "critical")

    notify.send_telegram(settings, alert, "W1AW: Locked out", 5)
    notify.send_webhook(settings, alert, "W1AW: Locked out", 5)

    assert posts[0][0] == "https://api.telegram.org/bot123:abc/sendMessage"
    assert json.loads(posts[0][1]) == {"chat_id": "-100", "text": "W1AW: Locked out\ndetails"}
    payload = json.loads(posts[1][1])
    assert payload["text"] == payload["content"] == "W1AW: Locked out\ndetails"
    assert payload["severity"] == "critical" and payload["key"] == "lockout"


def test_email(monkeypatch):
    sent = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            sent.append(("connect", host, port))

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def starttls(self, context):
            sent.append(("starttls",))

        def login(self, user, password):
            sent.append(("login", user, password))

        def send_message(self, message):
            sent.append(("send", message["Subject"], message["To"], message.get_content().strip()))

    monkeypatch.setattr(notify.smtplib, "SMTP", FakeSMTP)
    settings = NotificationSettings(
        smtp_host="smtp.example.com", smtp_username="me@example.com", smtp_password="pw", email_to="me@example.com"
    )

    notify.send_email(settings, Alert("x", "", "body text"), "W1AW: Something", 5)

    assert sent == [
        ("connect", "smtp.example.com", 587),
        ("starttls",),
        ("login", "me@example.com", "pw"),
        ("send", "W1AW: Something", "me@example.com", "body text"),
    ]


def make_client(senders=None):
    tmp_dir = Path(tempfile.mkdtemp())
    audit = AuditLog()
    notifier = Notifier(senders=senders or FakeSenders())
    service = RepeaterService(config=RepeaterConfig(callsign="W1AW"))
    app = create_app(
        service=service,
        start_background_tick=False,
        assets_store=AudioAssetStore(tmp_dir / "audio"),
        log_path=tmp_dir / "test.log",
        notifier=notifier,
        audit=audit,
    )
    return TestClient(app), notifier, audit


def test_secrets_are_write_only():
    client, notifier, audit = make_client()

    response = client.put("/api/alerts", json={"enabled": True, "ntfy_url": "https://ntfy.sh/t", "ntfy_token": "tk_secret"})

    assert response.status_code == 200
    body = response.json()
    assert "tk_secret" not in response.text
    assert body["secrets_set"]["ntfy_token"] is True and body["secrets_set"]["smtp_password"] is False
    assert body["channels"] == ["ntfy"]
    assert notifier.settings.ntfy_token == "tk_secret"
    entry = audit.recent(1)[0]
    assert entry.action == "PUT /api/alerts" and "tk_secret" not in entry.detail and "ntfy_token" in entry.detail
    assert "tk_secret" not in client.get("/api/config").text


def test_omitted_secrets_are_kept_and_empty_ones_cleared():
    client, notifier, _ = make_client()
    client.put("/api/alerts", json={"webhook_url": "https://example.com/hook", "smtp_password": "pw"})

    client.put("/api/alerts", json={"smtp_host": "smtp.example.com"})
    assert notifier.settings.webhook_url == "https://example.com/hook" and notifier.settings.smtp_password == "pw"

    client.put("/api/alerts", json={"webhook_url": ""})
    assert notifier.settings.webhook_url == ""


@pytest.mark.parametrize(
    "body",
    [{"ntfy_url": "ftp://x"}, {"email_to": "a@b.com\r\nBcc: c@d.com"}, {"telegram_chat_id": "abc"}, {"temperature_limit_c": 200}],
)
def test_bad_settings_are_refused(body):
    client, _, _ = make_client()
    assert client.put("/api/alerts", json=body).status_code == 422


def test_test_route():
    client, notifier, _ = make_client(FakeSenders(failing={"webhook"}))
    assert client.post("/api/alerts/test").status_code == 409

    client.put("/api/alerts", json={"ntfy_url": "https://ntfy.sh/t", "webhook_url": "https://example.com/hook"})
    response = client.post("/api/alerts/test")

    assert response.status_code == 200
    assert response.json()["results"] == {"ntfy": None, "webhook": "connection refused"}


def test_lockouts_raise_alerts():
    client, notifier, _ = make_client()
    service = client.app.state.service

    async def lock_out():
        service.lockout_hook(True)
        await asyncio.gather(*notifier._tasks)

    run(lock_out())

    assert notifier.recent[0]["title"] == "Locked out: stuck carrier"
    assert notifier.recent[0]["severity"] == "critical"


def test_alerts_page_lists_recent_alerts_and_system_readings():
    client, notifier, _ = make_client()
    run(notifier.notify(Alert("x", "Something", "details")))

    body = client.get("/api/alerts").json()

    assert body["recent"][0]["title"] == "Something" and body["recent"][0]["note"] == "alerts are off"
    assert set(body["system"]) >= {"temperature_c", "throttled", "disk", "sd_protection"}
