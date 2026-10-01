"""Alerts to the repeater owner: ntfy, Telegram, email or a webhook.

The settings, tokens and SMTP password included, live in a file of their own
in the data directory (owner-only, like every file `StateStore` writes) --
not in state.json, so neither GET /api/config nor backups ever carry them.
Sending happens in worker threads with a short timeout, never on the
controller tick, and is rate-limited so a flapping problem can't send
hundreds of messages.
"""
from __future__ import annotations

import asyncio
import base64
import collections
import dataclasses
import json
import logging
import smtplib
import socket
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Callable, Literal, Optional

from .persistence import StateStore

_logger = logging.getLogger("moreopenrepeater.alerts")

SEND_TIMEOUT_SECONDS = 10.0
REPEAT_AFTER_SECONDS = 30 * 60  # the same alert again only after this long
MAX_PER_HOUR = 12
RECENT_ALERTS = 30
SECRET_FIELDS = ("ntfy_token", "telegram_token", "smtp_password", "webhook_url")

Severity = Literal["info", "warning", "critical"]


@dataclass(frozen=True)
class NotificationSettings:
    enabled: bool = False
    ntfy_url: str = ""  # https://ntfy.sh/<topic>; anyone who knows the topic can read it
    ntfy_token: str = ""  # for a server with access control
    telegram_token: str = ""
    telegram_chat_id: str = ""
    email_to: str = ""  # comma-separated
    email_from: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_security: Literal["starttls", "ssl", "none"] = "starttls"
    smtp_username: str = ""
    smtp_password: str = ""
    webhook_url: str = ""  # gets JSON; Slack and Discord webhooks understand it as is
    temperature_limit_c: float = 80.0


_SETTINGS_FIELDS = {f.name for f in dataclasses.fields(NotificationSettings)}


@dataclass(frozen=True)
class Alert:
    key: str  # what it's about ("lockout", "temperature", ...), for de-duplication
    title: str
    message: str
    severity: Severity = "warning"


def configured_channels(settings: NotificationSettings) -> list[str]:
    channels = []
    if settings.ntfy_url:
        channels.append("ntfy")
    if settings.telegram_token and settings.telegram_chat_id:
        channels.append("telegram")
    if settings.smtp_host and settings.email_to:
        channels.append("email")
    if settings.webhook_url:
        channels.append("webhook")
    return channels


def _post(url: str, body: bytes, headers: dict[str, str], timeout: float) -> None:
    request = urllib.request.Request(url, data=body, headers={"User-Agent": "moreopenrepeater", **headers}, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        response.read()


def _header_text(text: str) -> str:
    """HTTP headers are Latin-1; ntfy decodes RFC 2047 for anything else."""
    try:
        text.encode("latin-1")
        return text
    except UnicodeEncodeError:
        return "=?UTF-8?B?" + base64.b64encode(text.encode()).decode() + "?="


_NTFY_PRIORITY = {"info": "default", "warning": "high", "critical": "urgent"}
_NTFY_TAGS = {"info": "information_source", "warning": "warning", "critical": "rotating_light"}


def send_ntfy(settings: NotificationSettings, alert: Alert, title: str, timeout: float) -> None:
    headers = {"Title": _header_text(title), "Priority": _NTFY_PRIORITY[alert.severity], "Tags": _NTFY_TAGS[alert.severity]}
    if settings.ntfy_token:
        headers["Authorization"] = f"Bearer {settings.ntfy_token}"
    _post(settings.ntfy_url, alert.message.encode(), headers, timeout)


def send_telegram(settings: NotificationSettings, alert: Alert, title: str, timeout: float) -> None:
    body = json.dumps({"chat_id": settings.telegram_chat_id, "text": f"{title}\n{alert.message}"}).encode()
    url = f"https://api.telegram.org/bot{settings.telegram_token}/sendMessage"
    _post(url, body, {"Content-Type": "application/json"}, timeout)


def send_email(settings: NotificationSettings, alert: Alert, title: str, timeout: float) -> None:
    message = EmailMessage()
    message["Subject"] = title
    message["From"] = settings.email_from or settings.smtp_username or f"moreopenrepeater@{socket.gethostname()}"
    message["To"] = settings.email_to
    message.set_content(alert.message)
    smtp_class = smtplib.SMTP_SSL if settings.smtp_security == "ssl" else smtplib.SMTP
    with smtp_class(settings.smtp_host, settings.smtp_port, timeout=timeout) as smtp:
        if settings.smtp_security == "starttls":
            smtp.starttls(context=ssl.create_default_context())
        if settings.smtp_username:
            smtp.login(settings.smtp_username, settings.smtp_password)
        smtp.send_message(message)


def send_webhook(settings: NotificationSettings, alert: Alert, title: str, timeout: float) -> None:
    text = f"{title}\n{alert.message}"
    body = {
        "title": title,
        "message": alert.message,
        "severity": alert.severity,
        "key": alert.key,
        "text": text,  # Slack
        "content": text,  # Discord
    }
    _post(settings.webhook_url, json.dumps(body).encode(), {"Content-Type": "application/json"}, timeout)


Sender = Callable[[NotificationSettings, Alert, str, float], None]
SENDERS: dict[str, Sender] = {"ntfy": send_ntfy, "telegram": send_telegram, "email": send_email, "webhook": send_webhook}


def describe_error(error: Exception) -> str:
    """Never the URL: Telegram's and most webhooks' carry the token."""
    reason = getattr(error, "reason", None)
    if isinstance(error, urllib.error.HTTPError):
        return f"HTTP {error.code} {error.reason}"
    if reason is not None:
        error = reason if isinstance(reason, Exception) else Exception(reason)
    if isinstance(error, OSError) and error.strerror:
        return error.strerror
    return str(error) or type(error).__name__


class Notifier:
    """`store=None` keeps the settings in memory (tests)."""

    def __init__(
        self,
        store: Optional[StateStore] = None,
        senders: Optional[dict[str, Sender]] = None,
        clock: Callable[[], float] = time.time,
        station: Callable[[], str] = lambda: "",
    ) -> None:
        self._store = store
        self._senders = senders if senders is not None else SENDERS
        self._clock = clock
        self.station = station
        self.audit_hook: Optional[Callable[[float, str, str], None]] = None  # (at, action, detail)
        self.recent: collections.deque[dict] = collections.deque(maxlen=RECENT_ALERTS)
        self._last_sent: dict[tuple[str, str], float] = {}
        self._sent_at: collections.deque[float] = collections.deque()
        self._tasks: set[asyncio.Task] = set()
        saved = store.load() if store is not None else None
        fields = {k: v for k, v in (saved or {}).items() if k in _SETTINGS_FIELDS}
        self.settings = NotificationSettings(**fields)

    @property
    def channels(self) -> list[str]:
        return configured_channels(self.settings)

    def update(self, **changes: object) -> NotificationSettings:
        self.settings = dataclasses.replace(self.settings, **changes)
        if self._store is not None:
            self._store.save(dataclasses.asdict(self.settings))
        return self.settings

    def title(self, alert: Alert) -> str:
        station = self.station()
        return f"{station}: {alert.title}" if station else alert.title

    def _allowed(self, alert: Alert, now: float) -> bool:
        last = self._last_sent.get((alert.key, alert.title))
        if last is not None and now - last < REPEAT_AFTER_SECONDS:
            return False
        while self._sent_at and now - self._sent_at[0] > 3600:
            self._sent_at.popleft()
        return len(self._sent_at) < MAX_PER_HOUR

    def post(self, alert: Alert) -> None:
        """Send from synchronous code on the event loop, without waiting."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            _logger.warning("alert %r not sent: no event loop", alert.title)
            return
        task = loop.create_task(self.notify(alert))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def notify(self, alert: Alert) -> None:
        now = self._clock()
        _logger.warning("alert: %s -- %s", alert.title, alert.message)
        channels = self.channels
        entry = {"at": now, "key": alert.key, "title": alert.title, "message": alert.message, "severity": alert.severity}
        if not (self.settings.enabled and channels):
            self.recent.appendleft({**entry, "sent": [], "errors": {}, "note": "alerts are off"})
            return
        if not self._allowed(alert, now):
            self.recent.appendleft({**entry, "sent": [], "errors": {}, "note": "not sent again so soon"})
            _logger.info("alert %r not sent: rate limit", alert.title)
            return
        self._last_sent[(alert.key, alert.title)] = now
        self._sent_at.append(now)
        errors = await self._send(alert, channels)
        sent = [c for c in channels if c not in errors]
        self.recent.appendleft({**entry, "sent": sent, "errors": errors, "note": ""})
        if self.audit_hook is not None:
            detail = f"{alert.title} → {', '.join(sent) or 'nowhere'}"
            if errors:
                detail += "; failed: " + ", ".join(f"{c} ({e})" for c, e in errors.items())
            self.audit_hook(now, "alert sent" if sent else "alert failed", detail)

    async def send_test(self) -> dict[str, Optional[str]]:
        """Every configured channel, whether alerts are on or not; channel -> error or None."""
        alert = Alert("test", "Test alert", "If you can read this, alerts from the repeater reach you.", "info")
        channels = self.channels
        errors = await self._send(alert, channels)
        return {channel: errors.get(channel) for channel in channels}

    async def _send(self, alert: Alert, channels: list[str]) -> dict[str, str]:
        loop = asyncio.get_running_loop()
        settings = self.settings
        title = self.title(alert)

        async def one(channel: str) -> Optional[str]:
            try:
                await loop.run_in_executor(None, self._senders[channel], settings, alert, title, SEND_TIMEOUT_SECONDS)
            except Exception as error:  # noqa: BLE001 -- one broken channel mustn't stop the others
                message = describe_error(error)
                _logger.error("couldn't send the alert by %s: %s", channel, message)
                return message
            return None

        results = await asyncio.gather(*(one(c) for c in channels))
        return {channel: error for channel, error in zip(channels, results) if error is not None}
