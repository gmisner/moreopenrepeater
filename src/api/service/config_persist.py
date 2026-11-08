"""Config updates, snapshot export/import, and change listeners."""
from __future__ import annotations

import dataclasses
import logging
from typing import TYPE_CHECKING, Callable

from controller.announcements import next_occurrence
from controller.config import RepeaterConfig
from controller.modes import effective_config

from .snapshot import announcements_from_snapshot, config_from_snapshot, macros_from_snapshot

if TYPE_CHECKING:
    from .facade import RepeaterService

_logger = logging.getLogger("moreopenrepeater.service")


class ConfigPersistence:
    """Saved settings, flat snapshot I/O, and config-change listeners."""

    def __init__(self, host: "RepeaterService", saved: RepeaterConfig) -> None:
        self._host = host
        self.saved = saved
        self._listeners: list[Callable[[RepeaterConfig], None]] = []

    def add_listener(self, listener: Callable[[RepeaterConfig], None]) -> None:
        self._listeners.append(listener)

    def update(self, **overrides: object) -> RepeaterConfig:
        """Change saved settings; returns the new saved settings."""
        _logger.info("update_config(%s)", overrides)
        self.apply(self.saved.replace_flat(**overrides), persist=True)
        return self.saved

    def apply(self, saved: RepeaterConfig, persist: bool = False) -> None:
        host = self._host
        if persist and host._state_store is not None:
            snapshot = self.export_snapshot()
            snapshot["config"] = saved.to_flat_dict()
            host._state_store.save(snapshot)
        self.saved = saved
        commands = host.controller.update_config(effective_config(saved, host.net_active), host._clock())
        self.changed()
        host._apply_commands(commands)

    def export_snapshot(self) -> dict:
        host = self._host
        return {
            "config": self.saved.to_flat_dict(),
            "macros": [dataclasses.asdict(m) for m in host.controller.list_macros()],
            "announcements": [dataclasses.asdict(a) for a in host.scheduler.list()],
        }

    def import_snapshot(self, data: dict) -> None:
        """Replace everything wholesale. Fields missing from `data` (e.g. a
        backup taken before a setting existed) get their defaults."""
        _logger.info("import_snapshot()")
        host = self._host
        saved = config_from_snapshot(data)
        macros = macros_from_snapshot(data)
        announcements = announcements_from_snapshot(data)
        now = host.wall_now()
        # Direct callers can import raw snapshots without HTTP model validation.
        for announcement in announcements:
            next_occurrence(announcement, now)
        if host._state_store is not None:
            host._state_store.save({
                "config": saved.to_flat_dict(),
                "macros": [dataclasses.asdict(macro) for macro in macros],
                "announcements": [dataclasses.asdict(announcement) for announcement in announcements],
            })
        host.controller.set_macros(macros)
        host.scheduler.set_announcements(announcements, now)
        self.apply(saved)

    def changed(self) -> None:
        host = self._host
        for listener in self._listeners:
            listener(host.controller.config)
        host._notify()

    def persist(self) -> None:
        if self._host._state_store is not None:
            self._host._state_store.save(self.export_snapshot())
