"""Multi-repeater hub: poll configured remote sites with per-site API tokens.

A hub is just another moreopenrepeater instance that keeps a list of remotes
in `data/hub-sites.json` and polls their API. Remotes never call home; if the
hub is down they keep running on their own. Each site uses its own Bearer
token (viewer can read status/activity; admin is needed for version/alerts).
"""
from __future__ import annotations

import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from typing import Callable, Optional
from urllib.parse import urlparse

import httpx

from .persistence import StateStore

DEFAULT_TIMEOUT = 5.0
_MAX_WORKERS = 8


class HubError(ValueError):
    """Bad site config from the dashboard."""


def normalize_base_url(url: str) -> str:
    cleaned = (url or "").strip().rstrip("/")
    parsed = urlparse(cleaned)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise HubError("Base URL must be an http(s) address, like https://repeater.example.com")
    if parsed.path not in ("", "/"):
        raise HubError("Base URL should be the site origin only (no /api path)")
    return cleaned


@dataclass(frozen=True)
class HubSite:
    id: str
    name: str
    base_url: str
    api_token: str

    @property
    def dashboard_url(self) -> str:
        return self.base_url


@dataclass(frozen=True)
class HubSiteSnapshot:
    id: str
    name: str
    base_url: str
    dashboard_url: str
    reachable: bool
    polled_at: float
    error: Optional[str] = None
    callsign: Optional[str] = None
    state: Optional[str] = None
    version: Optional[str] = None
    channel: Optional[str] = None
    last_activity: Optional[float] = None
    alerts_summary: Optional[str] = None


class HubSiteStore:
    """`store=None` keeps sites in memory (tests)."""

    def __init__(self, store: Optional[StateStore] = None) -> None:
        self._store = store
        self._lock = threading.Lock()
        data = store.load() if store else None
        self._sites: dict[str, HubSite] = {}
        for entry in (data or {}).get("sites", []):
            try:
                site = HubSite(
                    id=str(entry["id"]),
                    name=str(entry["name"]),
                    base_url=normalize_base_url(str(entry["base_url"])),
                    api_token=str(entry.get("api_token") or ""),
                )
            except (KeyError, TypeError, HubError):
                continue
            self._sites[site.id] = site

    def list(self) -> list[HubSite]:
        return sorted(self._sites.values(), key=lambda s: (s.name.lower(), s.id))

    def get(self, site_id: str) -> HubSite:
        return self._sites[site_id]

    def add(self, name: str, base_url: str, api_token: str) -> HubSite:
        name = name.strip()
        if not name:
            raise HubError("Name is required")
        token = api_token.strip()
        if not token:
            raise HubError("API token is required")
        url = normalize_base_url(base_url)
        with self._lock:
            site_id = secrets.token_hex(4)
            while site_id in self._sites:
                site_id = secrets.token_hex(4)
            site = HubSite(site_id, name, url, token)
            self._sites[site_id] = site
            self._save()
        return site

    def update(
        self,
        site_id: str,
        *,
        name: Optional[str] = None,
        base_url: Optional[str] = None,
        api_token: Optional[str] = None,
    ) -> HubSite:
        with self._lock:
            site = self._sites[site_id]
            changes: dict = {}
            if name is not None:
                cleaned = name.strip()
                if not cleaned:
                    raise HubError("Name is required")
                changes["name"] = cleaned
            if base_url is not None:
                changes["base_url"] = normalize_base_url(base_url)
            if api_token is not None:
                cleaned = api_token.strip()
                if cleaned:
                    changes["api_token"] = cleaned
            site = replace(site, **changes)
            self._sites[site_id] = site
            self._save()
        return site

    def remove(self, site_id: str) -> HubSite:
        with self._lock:
            site = self._sites.pop(site_id)
            self._save()
        return site

    def _save(self) -> None:
        if self._store is not None:
            self._store.save(
                {
                    "sites": [
                        {
                            "id": s.id,
                            "name": s.name,
                            "base_url": s.base_url,
                            "api_token": s.api_token,
                        }
                        for s in self.list()
                    ]
                }
            )


class HubPoller:
    """Fetches status / updates / activity / alerts from each remote site."""

    def __init__(
        self,
        client: Optional[httpx.Client] = None,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=timeout, follow_redirects=True)
        self._timeout = timeout
        self._clock = clock

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def poll(self, site: HubSite) -> HubSiteSnapshot:
        now = self._clock()
        headers = {"Authorization": f"Bearer {site.api_token}", "Accept": "application/json"}
        try:
            status = self._get_json(f"{site.base_url}/api/status", headers)
        except HubPollError as error:
            return HubSiteSnapshot(
                id=site.id,
                name=site.name,
                base_url=site.base_url,
                dashboard_url=site.dashboard_url,
                reachable=False,
                polled_at=now,
                error=str(error),
            )

        version, channel = self._version_channel(site.base_url, headers)
        last_activity = self._last_activity(site.base_url, headers)
        alerts_summary = self._alerts_summary(site.base_url, headers)
        return HubSiteSnapshot(
            id=site.id,
            name=site.name,
            base_url=site.base_url,
            dashboard_url=site.dashboard_url,
            reachable=True,
            polled_at=now,
            callsign=str(status.get("callsign") or "") or None,
            state=str(status.get("state") or "") or None,
            version=version,
            channel=channel,
            last_activity=last_activity,
            alerts_summary=alerts_summary,
        )

    def poll_all(self, sites: list[HubSite]) -> list[HubSiteSnapshot]:
        if not sites:
            return []
        if len(sites) == 1:
            return [self.poll(sites[0])]
        results: dict[str, HubSiteSnapshot] = {}
        workers = min(_MAX_WORKERS, len(sites))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(self.poll, site): site.id for site in sites}
            for future in as_completed(futures):
                site_id = futures[future]
                results[site_id] = future.result()
        return [results[site.id] for site in sites]

    def _version_channel(self, base_url: str, headers: dict) -> tuple[Optional[str], Optional[str]]:
        try:
            data = self._get_json(f"{base_url}/api/updates", headers)
        except HubPollError:
            return None, None
        channel = data.get("channel")
        version = data.get("version") or {}
        sha = version.get("sha") if isinstance(version, dict) else None
        if isinstance(sha, str) and sha:
            return sha[:7], str(channel) if channel else None
        return None, str(channel) if channel else None

    def _last_activity(self, base_url: str, headers: dict) -> Optional[float]:
        try:
            data = self._get_json(f"{base_url}/api/activity/transmissions", headers, params={"limit": 1})
        except HubPollError:
            return None
        if not isinstance(data, list) or not data:
            return None
        started = data[0].get("started_at")
        if isinstance(started, (int, float)):
            return float(started)
        return None

    def _alerts_summary(self, base_url: str, headers: dict) -> Optional[str]:
        try:
            data = self._get_json(f"{base_url}/api/alerts", headers)
        except HubPollError:
            return None
        recent = data.get("recent") or []
        if not isinstance(recent, list) or not recent:
            return "none"
        first = recent[0] if isinstance(recent[0], dict) else {}
        severity = first.get("severity") or "info"
        title = first.get("title") or "alert"
        if len(recent) == 1:
            return f"{severity}: {title}"
        return f"{severity}: {title} (+{len(recent) - 1})"

    def _get_json(self, url: str, headers: dict, params: Optional[dict] = None) -> dict | list:
        try:
            response = self._client.get(url, headers=headers, params=params, timeout=self._timeout)
        except httpx.TimeoutException as error:
            raise HubPollError("timed out") from error
        except httpx.HTTPError as error:
            raise HubPollError(f"unreachable ({error.__class__.__name__})") from error
        if response.status_code in (401, 403):
            raise HubPollError(f"auth failed ({response.status_code})")
        if response.status_code >= 400:
            raise HubPollError(f"HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError as error:
            raise HubPollError("invalid JSON") from error


class HubPollError(RuntimeError):
    """A single remote request failed."""
