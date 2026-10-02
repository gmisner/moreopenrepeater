"""Home Assistant from DTMF: a `homeassistant` macro calls a webhook trigger
or fires an event, and Home Assistant's own automations do the rest.

The macro's argument is a webhook ID (no token needed; the ID is the secret)
or `event:<type>`, which uses Home Assistant's REST API and needs a
long-lived access token in `MOREOPENREPEATER_HOMEASSISTANT_TOKEN`. The token
stays in the environment, out of the settings and backups.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import urllib.error
import urllib.request
from datetime import datetime
from typing import Callable, Optional
from urllib.parse import quote

from playout.renderer import TTS_PREFIX

from .service import RepeaterService

_logger = logging.getLogger("moreopenrepeater.homeassistant")

TOKEN_ENV = "MOREOPENREPEATER_HOMEASSISTANT_TOKEN"
TIMEOUT_SECONDS = 5.0
COOLDOWN_SECONDS = 10.0  # per target, so a held or repeated macro doesn't flap a light
EVENT_PREFIX = "event:"


class HomeAssistantError(Exception):
    pass


def _post(url: str, body: bytes, headers: dict[str, str], timeout: float) -> None:
    request = urllib.request.Request(url, data=body, headers={"User-Agent": "moreopenrepeater", **headers}, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        response.read()


class HomeAssistant:
    def __init__(
        self,
        service: RepeaterService,
        token: Optional[str] = None,
        post: Callable[[str, bytes, dict[str, str], float], None] = _post,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], datetime] = lambda: datetime.now().astimezone(),
    ) -> None:
        self._service = service
        self._token = token or None
        self._post = post
        self._clock = clock
        self._wall_clock = wall_clock
        self._last_sent: dict[str, float] = {}
        service.homeassistant_hook = self._dtmf

    @property
    def token_set(self) -> bool:
        return self._token is not None

    def send(self, target: str, data: dict) -> None:
        """POSTs `data` to the webhook or event `target`. Blocks; raises
        HomeAssistantError with a message fit for the dashboard."""
        base = self._service.config.homeassistant_url.strip().rstrip("/")
        if not base:
            raise HomeAssistantError("Home Assistant's address isn't set")
        headers = {"Content-Type": "application/json"}
        if target.startswith(EVENT_PREFIX):
            if self._token is None:
                raise HomeAssistantError(f"Firing events needs an access token in {TOKEN_ENV}")
            url = f"{base}/api/events/{quote(target[len(EVENT_PREFIX):], safe='')}"
            headers["Authorization"] = f"Bearer {self._token}"
        else:
            url = f"{base}/api/webhook/{quote(target, safe='')}"
        try:
            self._post(url, json.dumps(data).encode(), headers, TIMEOUT_SECONDS)
        except urllib.error.HTTPError as error:
            hint = " (check the access token)" if error.code == 401 else ""
            raise HomeAssistantError(f"Home Assistant answered {error.code}{hint}") from None
        except (urllib.error.URLError, OSError) as error:
            reason = getattr(error, "reason", error)
            raise HomeAssistantError(f"Couldn't reach Home Assistant: {reason}") from None

    def payload(self, pattern: str, source: str) -> dict:
        return {
            "pattern": pattern,
            "source": source,
            "repeater": self._service.config.callsign,
            "time": self._wall_clock().isoformat(timespec="seconds"),
        }

    def _dtmf(self, target: str, source: str, pattern: str) -> None:
        now = self._clock()
        if now - self._last_sent.get(target, float("-inf")) < COOLDOWN_SECONDS:
            _logger.info("ignored %s for %s: sent less than %d s ago", pattern, target, COOLDOWN_SECONDS)
            return
        self._last_sent[target] = now
        data = self.payload(pattern, source)

        async def run() -> None:
            try:
                await asyncio.get_running_loop().run_in_executor(None, self.send, target, data)
                spoken = "Done."
            except HomeAssistantError as error:
                _logger.warning("macro %s: %s", pattern, error)
                spoken = "Failed."
            if self._service.config.homeassistant_say_result:
                self._service.speak(TTS_PREFIX + spoken)

        asyncio.get_running_loop().create_task(run())
