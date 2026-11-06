"""Ask systemd to restart the service from the dashboard.

The service runs as an unprivileged user, so it can't restart itself. It
drops a request file; moreopenrepeater-restart.path (installed by
install-pi.sh) starts a root oneshot that removes the file and runs
`systemctl restart moreopenrepeater`.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional


@dataclass(frozen=True)
class RestartSettings:
    request_path: Path


def restart_settings_from_env(env: dict) -> Optional[RestartSettings]:
    """None unless installed by install-pi.sh, which sets the request path."""
    request_path = env.get("MOREOPENREPEATER_RESTART_REQUEST")
    if not request_path:
        return None
    return RestartSettings(Path(request_path))


class RestartControl:
    def __init__(self, settings: Optional[RestartSettings], clock: Callable[[], float] = time.time) -> None:
        self.settings = settings
        self._clock = clock
        self._requested_at: Optional[float] = None

    @property
    def available(self) -> bool:
        return self.settings is not None

    def status(self) -> dict:
        return {
            "available": self.available,
            "requested": self._requested_at is not None and self._clock() - self._requested_at < 60,
        }

    def request(self) -> None:
        if self.settings is None:
            raise RuntimeError("restart isn't set up on this install")
        path = self.settings.request_path
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text("restart\n")
        os.replace(tmp, path)  # the path unit must never see a half-written file
        self._requested_at = self._clock()
