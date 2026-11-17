"""What the rest of the API package may use from the repeater core.

Two protocols, split by who calls them (Stage 3 slice 7c-1, #229):

- `ApiCoreHandle`: what code that belongs in the API process uses (the routes,
  the app, backup and the support bundle, and the API-side modules such as nets,
  airport weather, site sensors and Home Assistant). A core running in its own
  process can serve exactly this much to a remote API.
- `CoreHandle`: everything. It adds what core-side code uses (the audio engines,
  GPIO, autopatch, AllStar and DMR audio, the link radio, the loops), which
  always runs in the core's own process next to the service.

`RepeaterService` satisfies both. Types only: nothing here changes behavior.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING, Callable, Optional, Protocol, runtime_checkable

from controller.announcements import Announcement
from controller.config import RepeaterConfig
from controller.events import ControllerEvent, SendLinkCommand
from controller.macros import Macro
from controller.state_machine import RepeaterController
from playout.renderer import ClipRenderer
from wx.nws import AlertTracker, WeatherAlert

from ..activity import ActivityRecorder
from .protocols import AudioOutput
from .snapshot import StatusSnapshot

if TYPE_CHECKING:
    from .links import LinkSupport
    from .patch import PatchSupport
    from .programming import Programming
    from .weather import WeatherSupport


class CoreError(Exception):
    """The core did not do what the API asked. Routes turn these into HTTP answers."""


class CoreUnavailable(CoreError):
    """The core could not be reached (or has not been heard from yet). Nothing was changed."""


class ConfigRefused(CoreError):
    """The core checked a configuration change and refused it. Nothing was applied.

    `code` is stable, `message` names settings and never values."""

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class CommandRefused(CoreError):
    """The core refused a command (a lockout that is not there, a simulator on a real radio).
    Nothing was done. `code` is stable."""

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


@runtime_checkable
class ApiCoreHandle(Protocol):
    """The members of the core that API-side code uses."""

    # Settings, read from the saved copy or as the controller currently runs them.
    @property
    def config(self) -> RepeaterConfig: ...

    @property
    def saved_config(self) -> RepeaterConfig: ...

    # Collaborators the routes and API-side modules call into. These are rich
    # objects, and the hardest to serve remotely (see the 7c design).
    @property
    def programming(self) -> Programming: ...

    @property
    def links(self) -> LinkSupport: ...

    weather_tracker: AlertTracker
    weather_alerts: list[WeatherAlert]
    weather_last_checked: Optional[datetime]
    weather_last_error: Optional[str]
    linked_nodes: set[str]

    # Live status.
    @property
    def locked_out(self) -> bool: ...

    def snapshot(self) -> StatusSnapshot: ...
    def subscribe(self) -> "asyncio.Queue[StatusSnapshot]": ...
    def unsubscribe(self, queue: "asyncio.Queue[StatusSnapshot]") -> None: ...
    def wall_now(self) -> datetime: ...

    # Changes.
    def update_config(self, **overrides: object) -> RepeaterConfig: ...

    async def change_config(self, **overrides: object) -> RepeaterConfig:
        """Change saved settings and return the new saved settings. In process this is
        `update_config`; against a remote core it waits for the core's answer, and raises
        `ConfigRefused` or `CoreUnavailable` instead of pretending it worked. Routes use
        this one."""
        ...
    async def command(self, name: str, **params: object) -> None:
        """Send the core a one-shot command: `clear_lockout`, `set_net_active(active)`,
        `simulate_cos(active)`, `simulate_ctcss(tone_hz)`, `simulate_dtmf(digit)` or
        `simulate_remote_keyed(node_id, keyed)`. Routes use this. Against a remote core it
        raises `CommandRefused` or `CoreUnavailable` rather than pretending it worked."""
        ...

    def clear_lockout(self) -> None: ...
    def set_net_active(self, active: bool) -> None: ...
    def held_reason(self, feature: str) -> str: ...
    def speak(self, clip: str, *, on_linked_radio: bool = False) -> None: ...
    def simulate_cos(self, active: bool) -> None: ...
    def simulate_ctcss(self, tone_hz: Optional[float]) -> None: ...
    def simulate_dtmf(self, digit: str) -> None: ...
    def simulate_remote_keyed(self, node_id: str, keyed: bool) -> None: ...

    # Macros and announcements.
    def list_macros(self) -> list[Macro]: ...
    def add_macro(self, macro: Macro) -> list[Macro]: ...
    def delete_macro(self, pattern: str) -> list[Macro]: ...
    def list_announcements(self) -> list[Announcement]: ...
    def next_announcement_run(self, announcement_id: str) -> Optional[datetime]: ...
    def save_announcement(self, announcement: Announcement) -> Announcement: ...
    def delete_announcement(self, announcement_id: str) -> None: ...
    def announcement_clip(self, announcement: Announcement) -> str: ...
    def weather_alert_clip(self, alert: WeatherAlert) -> str: ...

    # Backup and restore.
    def export_snapshot(self) -> dict: ...
    def import_snapshot(self, data: dict) -> None: ...


@runtime_checkable
class CoreHandle(ApiCoreHandle, Protocol):
    """Everything the API package uses from the core: `ApiCoreHandle` plus what
    core-side code uses, which always runs in the core's own process."""

    # Collaborators and state the audio engines, GPIO, autopatch and the links read.
    @property
    def patch(self) -> PatchSupport: ...

    @property
    def weather(self) -> WeatherSupport: ...

    activity: Optional[ActivityRecorder]
    renderer: Optional[ClipRenderer]
    audio_output: Optional[AudioOutput]
    link_radio_play: Optional[Callable[[str], None]]
    lockout_hook: Optional[Callable[[bool], None]]
    net_active: bool
    ptt_active: bool
    cos_active: bool
    ctcss_hz: Optional[float]
    link_audio: bool
    repeating: bool

    # Temporary: reach-through, to be replaced by narrow methods.
    controller: RepeaterController

    def add_config_listener(self, listener: Callable[[RepeaterConfig], None]) -> None: ...
    def set_link_command_sink(self, sink: Callable[[SendLinkCommand], None]) -> None: ...
    def idle_seconds(self) -> float: ...
    def notify_status(self) -> None: ...
    def tick(self) -> None: ...
    def handle_audio_events(self, events: list[ControllerEvent]) -> None: ...
    def handle_link_event(self, event: ControllerEvent) -> None: ...
    def link_radio_keyed(self, keyed: bool) -> None: ...
    def run_macro(self, pattern: str, source: str) -> bool: ...
    def begin_patch(self) -> None: ...
    def end_patch(self) -> None: ...
    def due_announcements(self) -> list[tuple[str, bool]]: ...
    def queue_announcement(self, clip: str, *, on_linked_radio: bool = False, priority: int = 20) -> bool: ...
    def defer_announcement(self, clip: str, *, on_linked_radio: bool = False) -> None: ...
    def weather_polled(self, geojson: dict, announce: bool = True) -> list[str]: ...
    def weather_poll_failed(self, error: str) -> None: ...
    def weather_schedule_due(self) -> bool: ...
    def scheduled_weather_text(self) -> Optional[str]: ...
