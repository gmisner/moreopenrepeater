"""Read the `logd` collector's history from the dashboard API (#221, #229).

`logd` keeps structured events searchable for weeks; this is the API's way to ask it. It is a plain
client of logd's query socket (HTTP over a Unix socket, GET only) and holds nothing of its own: if the
socket is not set, or logd is down, the history is unavailable and says so. The live log, the log file
and the support bundle's other files do not depend on it.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping, Optional

import httpx

SOCKET_VARIABLE = "MOREOPENREPEATER_LOGD_QUERY_SOCKET"
TIMEOUT_SECONDS = 6.0  # logd gives a search 5 s; a little more than that
EVENT_PARAMS = ("q", "min_severity", "module", "event", "correlation_id", "since", "until", "limit", "before")


class LogHistoryUnavailable(Exception):
    """logd is not set up for this API, or did not answer."""

    def __init__(self, message: str, *, configured: bool = True) -> None:
        super().__init__(message)
        self.configured = configured


class LogHistoryRefused(Exception):
    """logd understood the request and said no (a bad search, for example)."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class LogHistory:
    def __init__(self, socket_path: Optional[Path]) -> None:
        self.socket_path = socket_path

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "LogHistory":
        value = (os.environ if env is None else env).get(SOCKET_VARIABLE, "").strip()
        return cls(Path(value) if value else None)

    @property
    def configured(self) -> bool:
        return self.socket_path is not None

    def _get(self, path: str, params: Optional[Mapping[str, Any]] = None) -> Any:
        if self.socket_path is None:
            raise LogHistoryUnavailable("the log collector is not set up on this install", configured=False)
        # Plain HTTP over a Unix socket: verify=False skips building a TLS context nobody uses.
        transport = httpx.HTTPTransport(uds=str(self.socket_path), verify=False)
        try:
            with httpx.Client(transport=transport, base_url="http://logd", timeout=TIMEOUT_SECONDS) as client:
                response = client.get(path, params={k: v for k, v in (params or {}).items() if v not in (None, "")})
        except httpx.TimeoutException:
            raise LogHistoryUnavailable("the log collector did not answer in time") from None
        except httpx.HTTPError:
            raise LogHistoryUnavailable("the log collector is not reachable") from None
        try:
            body = response.json()
        except ValueError:
            raise LogHistoryUnavailable("the log collector's answer was not understood") from None
        if 400 <= response.status_code < 500:
            raise LogHistoryRefused(response.status_code, str(body.get("error", "request refused")) if isinstance(body, dict) else "request refused")
        if response.status_code != 200:
            raise LogHistoryUnavailable(f"the log collector answered {response.status_code}")
        return body

    def events(self, **params: Any) -> dict:
        """Events newest first, `{"events": [...], "next_before": id | None}`."""
        return self._get("/events", {key: params.get(key) for key in EVENT_PARAMS})

    def gaps(self, **params: Any) -> dict:
        """What the collector knows is missing from the record: `{"gaps": [...]}`."""
        return self._get("/gaps", {"limit": params.get("limit")})

    def stats(self) -> dict:
        return self._get("/stats")

    def status(self) -> dict:
        """Always an answer, for a page deciding whether to show the history at all."""
        if not self.configured:
            return {"configured": False, "available": False, "reason": "the log collector is not set up on this install"}
        try:
            return {"configured": True, "available": True, "stats": self.stats()}
        except LogHistoryUnavailable as error:
            return {"configured": True, "available": False, "reason": str(error)}
        except LogHistoryRefused as error:
            return {"configured": True, "available": False, "reason": str(error)}
