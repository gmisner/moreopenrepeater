"""Bridges a RepeaterController to the API/dashboard layer.

Runs the controller in-process on the same asyncio event loop as FastAPI --
all mutation happens synchronously within request handlers or the periodic
tick loop, so no locking is needed. Exposes simulate_* methods that inject
synthetic events (COS, CTCSS, DTMF, remote-node keying) so the dashboard is
usable for development/demo without real audio_io/link hardware wired up
yet; those are also the seam audio_io/link will eventually feed instead.
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging
import time
from datetime import datetime, timedelta
from typing import Callable, Optional

from controller.announcements import Announcement, AnnouncementScheduler, announcement_from_dict
from controller.events import (
    AssertPTT,
    COSChanged,
    CTCSSChanged,
    ControllerCommand,
    ControllerEvent,
    DTMFDigit,
    LinkStateChanged,
    PlayAudio,
    RemoteKeyed,
    SendLinkCommand,
)
from controller.macros import Macro
from controller.state_machine import RepeaterConfig, RepeaterController

from playout.renderer import ASSET_PREFIX, TTS_PREFIX, ClipRenderer
from wx.nws import AlertTracker, WeatherAlert, meets_severity, parse_alerts, speech_text

from .persistence import StateStore

_logger = logging.getLogger("moreopenrepeater.service")

_CONFIG_FIELDS = {f.name for f in dataclasses.fields(RepeaterConfig)}
_MACRO_FIELDS = {f.name for f in dataclasses.fields(Macro)}


def _known_fields(data: dict, known: set[str], what: str) -> dict:
    """Saved state and backups may come from an older or newer version of
    this app; drop fields this version doesn't know instead of crashing, and
    let missing ones fall back to their defaults."""
    unknown = set(data) - known
    if unknown:
        _logger.warning("ignoring unknown %s fields: %s", what, sorted(unknown))
    return {k: v for k, v in data.items() if k in known}


def config_from_snapshot(data: dict) -> RepeaterConfig:
    return RepeaterConfig(**_known_fields(data.get("config", {}), _CONFIG_FIELDS, "config"))


def macros_from_snapshot(data: dict) -> list[Macro]:
    return [Macro(**_known_fields(m, _MACRO_FIELDS, "macro")) for m in data.get("macros", [])]


def announcements_from_snapshot(data: dict) -> list[Announcement]:
    return [announcement_from_dict(a) for a in data.get("announcements", [])]


@dataclasses.dataclass(frozen=True)
class StatusSnapshot:
    state: str
    ptt_active: bool
    cos_active: bool
    ctcss_hz: Optional[float]
    linked_nodes: list[str]
    last_clip: Optional[str]
    timestamp: float


class RepeaterService:
    def __init__(
        self,
        config: Optional[RepeaterConfig] = None,
        macros: Optional[list[Macro]] = None,
        clock: Callable[[], float] = time.monotonic,
        link_command_sink: Callable[[SendLinkCommand], None] = lambda command: None,
        state_store: Optional[StateStore] = None,
        renderer: Optional[ClipRenderer] = None,
        announcements: Optional[list[Announcement]] = None,
        wall_clock: Callable[[], datetime] = datetime.now,
    ) -> None:
        """`clock` is monotonic and drives the controller's timers;
        `wall_clock` is naive local time and drives scheduled announcements."""
        self._clock = clock
        self._wall_clock = wall_clock
        self._link_command_sink = link_command_sink
        self._state_store = state_store
        self.renderer = renderer
        self._config_listeners: list[Callable[[RepeaterConfig], None]] = []
        saved = state_store.load() if state_store is not None else None
        if saved is not None:
            config = config_from_snapshot(saved)
            macros = macros_from_snapshot(saved)
            announcements = announcements_from_snapshot(saved)
            _logger.info("loaded saved state from %s", state_store.path)
        self.controller = RepeaterController(
            config or RepeaterConfig(), macros=macros or [], now=clock(), clip_duration=self._clip_duration
        )
        self.scheduler = AnnouncementScheduler(announcements or [], wall_clock())
        self.weather_tracker = AlertTracker()
        self.weather_alerts: list[WeatherAlert] = []
        self.weather_last_checked: Optional[datetime] = None
        self.weather_last_error: Optional[str] = None
        self.ptt_active = False
        self.cos_active = False
        self.ctcss_hz: Optional[float] = None
        self.linked_nodes: set[str] = set()
        self.last_clip: Optional[str] = None
        self._subscribers: set["asyncio.Queue[StatusSnapshot]"] = set()

    @property
    def config(self) -> RepeaterConfig:
        return self.controller.config

    def set_link_command_sink(self, sink: Callable[[SendLinkCommand], None]) -> None:
        self._link_command_sink = sink

    def add_config_listener(self, listener: Callable[[RepeaterConfig], None]) -> None:
        self._config_listeners.append(listener)

    def _clip_duration(self, clip: str) -> Optional[float]:
        if self.renderer is None:
            return None
        return self.renderer.cached_duration(clip, self.controller.config)

    def snapshot(self) -> StatusSnapshot:
        return StatusSnapshot(
            state=self.controller.state,
            ptt_active=self.ptt_active,
            cos_active=self.cos_active,
            ctcss_hz=self.ctcss_hz,
            linked_nodes=sorted(self.linked_nodes),
            last_clip=self.last_clip,
            timestamp=self._clock(),
        )

    def subscribe(self) -> "asyncio.Queue[StatusSnapshot]":
        queue: "asyncio.Queue[StatusSnapshot]" = asyncio.Queue()
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: "asyncio.Queue[StatusSnapshot]") -> None:
        self._subscribers.discard(queue)

    def _notify(self) -> None:
        snapshot = self.snapshot()
        for queue in self._subscribers:
            queue.put_nowait(snapshot)

    def _apply_commands(self, commands: list[ControllerCommand]) -> None:
        for command in commands:
            if isinstance(command, AssertPTT):
                self.ptt_active = command.active
            elif isinstance(command, PlayAudio):
                self.last_clip = command.clip
            elif isinstance(command, SendLinkCommand):
                self._link_command_sink(command)
        self._notify()

    def tick(self) -> None:
        self._apply_commands(self.controller.tick(self._clock()))

    def simulate_cos(self, active: bool) -> None:
        _logger.info("simulate_cos(active=%s)", active)
        self.cos_active = active
        self._apply_commands(self.controller.handle_event(COSChanged(active=active), self._clock()))

    def simulate_ctcss(self, tone_hz: Optional[float]) -> None:
        _logger.info("simulate_ctcss(tone_hz=%s)", tone_hz)
        self.ctcss_hz = tone_hz
        self._apply_commands(self.controller.handle_event(CTCSSChanged(tone_hz=tone_hz), self._clock()))

    def simulate_dtmf(self, digit: str) -> None:
        _logger.info("simulate_dtmf(digit=%r)", digit)
        self._apply_commands(self.controller.handle_event(DTMFDigit(digit=digit), self._clock()))

    def simulate_remote_keyed(self, node_id: str, keyed: bool) -> None:
        _logger.info("simulate_remote_keyed(node_id=%r, keyed=%s)", node_id, keyed)
        if keyed:
            self.linked_nodes.add(node_id)
        else:
            self.linked_nodes.discard(node_id)
        self._apply_commands(self.controller.handle_event(RemoteKeyed(node_id=node_id, keyed=keyed), self._clock()))

    def handle_link_event(self, event: ControllerEvent) -> None:
        """Apply a real event from `link.node_link.NodeLinkClient` -- the
        same shape as simulate_remote_keyed's bookkeeping, but driven by the
        network layer instead of a dashboard button."""
        _logger.info("handle_link_event(%r)", event)
        if isinstance(event, LinkStateChanged):
            if event.linked:
                self.linked_nodes.add(event.node_id)
            else:
                self.linked_nodes.discard(event.node_id)
        self._apply_commands(self.controller.handle_event(event, self._clock()))

    def update_config(self, **overrides: object) -> RepeaterConfig:
        _logger.info("update_config(%s)", overrides)
        new_config = dataclasses.replace(self.controller.config, **overrides)
        self.controller.update_config(new_config, self._clock())
        self._persist()
        self._config_changed()
        return new_config

    def list_macros(self) -> list[Macro]:
        return self.controller.list_macros()

    def add_macro(self, macro: Macro) -> list[Macro]:
        _logger.info("add_macro(pattern=%r, command=%r)", macro.pattern, macro.command)
        macros = [m for m in self.controller.list_macros() if m.pattern != macro.pattern]
        macros.append(macro)
        self.controller.set_macros(macros)
        self._persist()
        return self.controller.list_macros()

    def delete_macro(self, pattern: str) -> list[Macro]:
        _logger.info("delete_macro(pattern=%r)", pattern)
        macros = [m for m in self.controller.list_macros() if m.pattern != pattern]
        self.controller.set_macros(macros)
        self._persist()
        return macros

    def list_announcements(self) -> list[Announcement]:
        return self.scheduler.list()

    def next_announcement_run(self, announcement_id: str) -> Optional[datetime]:
        return self.scheduler.next_run(announcement_id)

    def save_announcement(self, announcement: Announcement) -> Announcement:
        _logger.info("save_announcement(id=%r, name=%r)", announcement.id, announcement.name)
        self.scheduler.upsert(announcement, self._wall_clock())
        self._persist()
        return announcement

    def delete_announcement(self, announcement_id: str) -> None:
        _logger.info("delete_announcement(id=%r)", announcement_id)
        self.scheduler.remove(announcement_id, self._wall_clock())
        self._persist()

    def announcement_clip(self, announcement: Announcement) -> str:
        """The controller clip for an announcement: its uploaded recording,
        or its message through text-to-speech."""
        if announcement.asset_id:
            return ASSET_PREFIX + announcement.asset_id
        return TTS_PREFIX + announcement.message

    def due_announcement_clips(self) -> list[str]:
        due = self.scheduler.due(self._wall_clock())
        for announcement in due:
            _logger.info("announcement %r is due", announcement.name)
        return [self.announcement_clip(a) for a in due]

    def queue_announcement(self, clip: str) -> bool:
        """Callers should render `clip` first (off the event loop) so the
        controller knows how long to hold PTT for."""
        return self.controller.queue_announcement(clip)

    def weather_polled(self, geojson: dict, announce: bool = True) -> list[str]:
        """Record a successful NWS poll; return the clips to announce.
        With `announce=False` (a manual check while alerts are switched
        off) it only refreshes what the dashboard shows."""
        config = self.controller.config
        alerts = parse_alerts(geojson)
        self.weather_alerts = [a for a in alerts if meets_severity(a, config.wx_min_severity)]
        self.weather_last_checked = self._wall_clock()
        self.weather_last_error = None
        if not announce:
            return []
        repeat = timedelta(minutes=config.wx_repeat_minutes) if config.wx_repeat_minutes > 0 else None
        to_announce = self.weather_tracker.update(alerts, self._wall_clock(), config.wx_min_severity, repeat)
        for alert in to_announce:
            _logger.info("announcing weather alert: %s (%s)", alert.event, alert.id)
        return [self.weather_alert_clip(a) for a in to_announce]

    def weather_poll_failed(self, error: str) -> None:
        self.weather_last_checked = self._wall_clock()
        self.weather_last_error = error

    def weather_alert_clip(self, alert: WeatherAlert) -> str:
        return TTS_PREFIX + speech_text(alert)

    def export_snapshot(self) -> dict:
        return {
            "config": dataclasses.asdict(self.controller.config),
            "macros": [dataclasses.asdict(m) for m in self.controller.list_macros()],
            "announcements": [dataclasses.asdict(a) for a in self.scheduler.list()],
        }

    def import_snapshot(self, data: dict) -> None:
        """Replace everything wholesale. Fields missing from `data` (e.g. a
        backup taken before a setting existed) get their defaults."""
        _logger.info("import_snapshot()")
        self.controller.update_config(config_from_snapshot(data), self._clock())
        self.controller.set_macros(macros_from_snapshot(data))
        self.scheduler.set_announcements(announcements_from_snapshot(data), self._wall_clock())
        self._persist()
        self._config_changed()

    def _config_changed(self) -> None:
        for listener in self._config_listeners:
            listener(self.controller.config)
        self._notify()

    def _persist(self) -> None:
        if self._state_store is not None:
            self._state_store.save(self.export_snapshot())
