"""Ask systemd to install / restart / stop the DVSwitch DMR bridges.

Same path-unit pattern as api.tailscale and api.restart: the unprivileged
service writes a request file; moreopenrepeater-dmr-restart.path starts a
root oneshot (scripts/dmr-restart.sh). Install progress is in
/var/lib/moreopenrepeater-dmr/status.json.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal, Optional

Action = Literal["restart", "stop", "install"]
REQUEST_PICKUP_SECONDS = 60
STALLED_AFTER_SECONDS = 45 * 60
START_SLACK_SECONDS = 5
DEFAULT_STATUS_DIR = Path("/var/lib/moreopenrepeater-dmr")


@dataclass(frozen=True)
class DmrRestartSettings:
    request_path: Path
    status_dir: Path = DEFAULT_STATUS_DIR


def dmr_restart_settings_from_env(env: dict) -> Optional[DmrRestartSettings]:
    request_path = env.get("MOREOPENREPEATER_DMR_RESTART_REQUEST")
    if not request_path:
        return None
    status_dir = env.get("MOREOPENREPEATER_DMR_STATUS")
    return DmrRestartSettings(Path(request_path), Path(status_dir) if status_dir else DEFAULT_STATUS_DIR)


def read_helper_status(settings: DmrRestartSettings) -> Optional[dict]:
    try:
        status = json.loads((settings.status_dir / "status.json").read_text())
    except (OSError, ValueError):
        return None
    return status if isinstance(status, dict) else None


class DmrRestartControl:
    def __init__(self, settings: Optional[DmrRestartSettings], clock: Callable[[], float] = time.time) -> None:
        self.settings = settings
        self._clock = clock
        self._requested_at: Optional[float] = None
        self._last_action: Optional[Action] = None

    @property
    def available(self) -> bool:
        return self.settings is not None

    def helper_status(self) -> Optional[dict]:
        if self.settings is None:
            return None
        status = read_helper_status(self.settings)
        now = self._clock()
        if self._requested_at is not None and (
            status is None or float(status.get("started_at") or 0) < self._requested_at - START_SLACK_SECONDS
        ):
            if now - self._requested_at < REQUEST_PICKUP_SECONDS:
                return {"state": "requested", "action": self._last_action, "started_at": self._requested_at}
            return {
                "state": "failed",
                "action": self._last_action,
                "started_at": self._requested_at,
                "message": "The DMR helper didn't pick up the request. Is moreopenrepeater-dmr-restart.path enabled?",
            }
        if status and status.get("state") == "running" and now - float(status.get("started_at") or now) > STALLED_AFTER_SECONDS:
            return {**status, "state": "failed", "message": "The DMR change has been running for too long; see the log."}
        return status

    def busy(self) -> bool:
        status = self.helper_status()
        return status is not None and status.get("state") in ("requested", "running")

    def status(self) -> dict:
        helper = self.helper_status()
        return {
            "available": self.available,
            "requested": self.busy(),
            "last_action": self._last_action or (helper or {}).get("action"),
            "state": (helper or {}).get("state"),
            "message": (helper or {}).get("message"),
        }

    def request(self, action: Action = "restart") -> None:
        if self.settings is None:
            raise RuntimeError("DMR service restart isn't set up on this install (needs install-pi.sh)")
        if action not in ("restart", "stop", "install"):
            raise ValueError(f"unknown action {action!r}")
        if action == "install" and self.busy():
            raise RuntimeError("A DMR install or restart is already in progress")
        path = self.settings.request_path
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(f"{action}\n")
        os.replace(tmp, path)
        self._requested_at = self._clock()
        self._last_action = action
