"""Tailscale install, login, and Serve for remote dashboard access.

The service runs unprivileged, so install / up / serve / logout go through a
request file that moreopenrepeater-tailscale.path picks up as root
(scripts/tailscale.sh), the same pattern as Updates. After `up` sets
--operator=moreopenrepeater, live status comes from `tailscale status` and
`tailscale serve status` as the service user. Auth keys are never kept in
status or logs.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

ACTIONS = frozenset({"install", "up", "serve-on", "serve-off", "down"})
AUTH_KEY_RE = re.compile(r"^tskey-[A-Za-z0-9_-]+$")
REQUEST_PICKUP_SECONDS = 60
STALLED_AFTER_SECONDS = 45 * 60
START_SLACK_SECONDS = 5
MAX_LOG_LINES = 400
MAX_AUTH_KEY_LEN = 200


@dataclass(frozen=True)
class TailscaleSettings:
    status_dir: Path
    request_path: Path


def tailscale_settings_from_env(env: dict) -> Optional[TailscaleSettings]:
    """None unless installed by install-pi.sh, which sets both in the unit."""
    status_dir = env.get("MOREOPENREPEATER_TAILSCALE")
    request_path = env.get("MOREOPENREPEATER_TAILSCALE_REQUEST")
    if not status_dir or not request_path:
        return None
    return TailscaleSettings(Path(status_dir), Path(request_path))


def dashboard_port(env: Optional[dict] = None) -> int:
    raw = (env or os.environ).get("MOREOPENREPEATER_PORT", "8000")
    try:
        port = int(raw)
    except (TypeError, ValueError):
        return 8000
    return port if 1 <= port <= 65535 else 8000


def read_helper_status(settings: TailscaleSettings) -> Optional[dict]:
    try:
        status = json.loads((settings.status_dir / "status.json").read_text())
    except (OSError, ValueError):
        return None
    return status if isinstance(status, dict) else None


def read_log(settings: TailscaleSettings, lines: int = MAX_LOG_LINES) -> list[str]:
    try:
        text = (settings.status_dir / "tailscale.log").read_text(errors="replace")
    except OSError:
        return []
    return text.splitlines()[-lines:]


def _run_json(args: list[str], timeout: float = 5.0) -> Optional[dict]:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def cli_installed() -> bool:
    return shutil.which("tailscale") is not None


def _dns_name(status: dict) -> Optional[str]:
    self = status.get("Self") or {}
    name = (self.get("DNSName") or status.get("Self", {}).get("DNSName") or "").rstrip(".")
    if name:
        return name
    # Older status JSON put the name on the top-level MagicDNSSuffix + HostName.
    host = (self.get("HostName") or "").strip()
    suffix = (status.get("MagicDNSSuffix") or "").strip().rstrip(".")
    if host and suffix:
        return f"{host}.{suffix}"
    return None


def _serve_url(serve: Optional[dict], dns_name: Optional[str]) -> Optional[str]:
    """Best-effort HTTPS URL from `tailscale serve status --json`."""
    if not serve or not dns_name:
        return None
    # Current JSON nests Web handlers under the node's hostname / TCP map.
    # Prefer an https:// URL if present in the structure; else assume Serve
    # on 443 for this node.
    blob = json.dumps(serve)
    if f"https://{dns_name}" in blob or '"HTTPS"' in blob or "https" in blob.lower():
        return f"https://{dns_name}"
    # Empty config means Serve is off.
    if serve in ({}, {"TCP": {}, "Web": {}, "Foreground": {}}):
        return None
    if serve.get("Web") or serve.get("TCP") or serve.get("Foreground"):
        return f"https://{dns_name}"
    return None


def _serve_enabled(serve: Optional[dict]) -> bool:
    if not serve:
        return False
    if serve.get("Web") or serve.get("TCP") or serve.get("Foreground"):
        return True
    # Some builds return a flat map of ports; treat non-empty as on.
    return bool(serve) and serve not in ({}, {"TCP": {}, "Web": {}, "Foreground": {}})


def probe_tailscale(run_json: Callable[[list[str]], Optional[dict]] = _run_json) -> dict:
    """Live Tailscale state from the CLI (needs operator or root)."""
    if not cli_installed():
        return {
            "installed": False,
            "backend_state": None,
            "dns_name": None,
            "auth_url": None,
            "serve_enabled": False,
            "serve_url": None,
            "cli_error": None,
        }
    status = run_json(["tailscale", "status", "--json"])
    if status is None:
        return {
            "installed": True,
            "backend_state": None,
            "dns_name": None,
            "auth_url": None,
            "serve_enabled": False,
            "serve_url": None,
            "cli_error": "Couldn't read Tailscale status. If you just installed it, join the tailnet first.",
        }
    serve = run_json(["tailscale", "serve", "status", "--json"])
    dns_name = _dns_name(status)
    return {
        "installed": True,
        "backend_state": status.get("BackendState") or None,
        "dns_name": dns_name,
        "auth_url": status.get("AuthURL") or None,
        "serve_enabled": _serve_enabled(serve),
        "serve_url": _serve_url(serve, dns_name),
        "cli_error": None,
    }


def validate_auth_key(auth_key: Optional[str]) -> Optional[str]:
    if auth_key is None:
        return None
    key = auth_key.strip()
    if not key:
        return None
    if len(key) > MAX_AUTH_KEY_LEN or not AUTH_KEY_RE.fullmatch(key):
        raise ValueError("That doesn't look like a Tailscale auth key (tskey-…).")
    return key


class TailscaleControl:
    def __init__(
        self,
        settings: Optional[TailscaleSettings],
        *,
        clock: Callable[[], float] = time.time,
        run_json: Callable[[list[str]], Optional[dict]] = _run_json,
    ) -> None:
        self.settings = settings
        self._clock = clock
        self._run_json = run_json
        self._requested_at: Optional[float] = None

    @property
    def available(self) -> bool:
        return self.settings is not None

    def helper_status(self) -> Optional[dict]:
        if self.settings is None:
            return None
        status = read_helper_status(self.settings)
        now = self._clock()
        if self._requested_at is not None and (
            status is None or status.get("started_at", 0) < self._requested_at - START_SLACK_SECONDS
        ):
            if now - self._requested_at < REQUEST_PICKUP_SECONDS:
                return {"state": "requested", "started_at": self._requested_at}
            return {
                "state": "failed",
                "started_at": self._requested_at,
                "message": "The Tailscale helper didn't pick up the request. Is moreopenrepeater-tailscale.path enabled?",
            }
        if status and status.get("state") == "running" and now - status.get("started_at", now) > STALLED_AFTER_SECONDS:
            return {**status, "state": "failed", "message": "The Tailscale change has been running for too long; see the log."}
        return status

    def busy(self) -> bool:
        status = self.helper_status()
        return status is not None and status.get("state") in ("requested", "running")

    def snapshot(self) -> dict:
        helper = self.helper_status()
        live = probe_tailscale(self._run_json)
        # Prefer AuthURL from an in-progress interactive login written by the helper.
        auth_url = live.get("auth_url") or (helper or {}).get("auth_url")
        return {
            "available": self.available,
            "installed": live["installed"],
            "backend_state": live["backend_state"],
            "dns_name": live["dns_name"],
            "auth_url": auth_url,
            "serve_enabled": live["serve_enabled"],
            "serve_url": live["serve_url"],
            "port": dashboard_port(),
            "cli_error": live["cli_error"],
            "status": helper,
        }

    def request(self, action: str, auth_key: Optional[str] = None) -> None:
        if self.settings is None:
            raise RuntimeError("Tailscale remote access isn't set up on this install")
        if action not in ACTIONS:
            raise ValueError(f"unknown action {action!r}")
        key = validate_auth_key(auth_key)
        if key and action != "up":
            raise ValueError("auth keys are only used when joining the tailnet")
        payload: dict = {"action": action}
        if key:
            payload["auth_key"] = key
        path = self.settings.request_path
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(payload) + "\n")
        os.replace(tmp, path)
        self._requested_at = self._clock()
