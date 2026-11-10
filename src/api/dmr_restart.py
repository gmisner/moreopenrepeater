"""Ask systemd to restart (or stop) the DVSwitch DMR bridges.

Same path-unit pattern as api.restart: the unprivileged service writes a
request file; moreopenrepeater-dmr-restart.path starts a root oneshot.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal, Optional

Action = Literal["restart", "stop"]


@dataclass(frozen=True)
class DmrRestartSettings:
    request_path: Path


def dmr_restart_settings_from_env(env: dict) -> Optional[DmrRestartSettings]:
    request_path = env.get("MOREOPENREPEATER_DMR_RESTART_REQUEST")
    if not request_path:
        return None
    return DmrRestartSettings(Path(request_path))


class DmrRestartControl:
    def __init__(self, settings: Optional[DmrRestartSettings], clock: Callable[[], float] = time.time) -> None:
        self.settings = settings
        self._clock = clock
        self._requested_at: Optional[float] = None
        self._last_action: Optional[Action] = None

    @property
    def available(self) -> bool:
        return self.settings is not None

    def status(self) -> dict:
        return {
            "available": self.available,
            "requested": self._requested_at is not None and self._clock() - self._requested_at < 60,
            "last_action": self._last_action,
        }

    def request(self, action: Action = "restart") -> None:
        if self.settings is None:
            raise RuntimeError("DMR service restart isn't set up on this install (run install-pi.sh --dmr)")
        path = self.settings.request_path
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(f"{action}\n")
        os.replace(tmp, path)
        self._requested_at = self._clock()
        self._last_action = action
