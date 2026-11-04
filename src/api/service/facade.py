"""Thin orchestration facade over repeater feature collaborators.

Owns the controller tick loop, status snapshot/pubsub, and wiring. Feature
clusters (programming, weather, patch, config, links) live in sibling modules.
External call sites keep using this class; hooks that macros invoke are stored
on Programming (and patch calls on PatchSupport) with attribute forwarders here.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import Callable, Optional, Union

from controller.announcements import Announcement, AnnouncementScheduler
from controller.events import (
    AssertPTT,
    CodedCommand,
    COSChanged,
    CTCSSChanged,
    ControllerCommand,
    ControllerEvent,
    DialPatch,
    DTMFDigit,
    HangupPatch,
    MailboxCommand,
    PlayAudio,
    RunAction,
    SendLinkCommand,
)
from controller.macros import Macro
from controller.modes import effective_config, held_reason
from controller.config import RepeaterConfig
from controller.state_machine import IDLE, PATCH, RECEIVING, RepeaterController
from playout.renderer import ClipRenderer
from wx.nws import WeatherAlert

from ..activity import ActivityRecorder
from ..persistence import StateStore
from .config_persist import ConfigPersistence
from .links import LinkSupport
from .patch import PatchSupport
from .programming import Programming
from .protocols import AudioOutput, PatchCalls
from .snapshot import (
    StatusSnapshot,
    announcements_from_snapshot,
    config_from_snapshot,
    macros_from_snapshot,
)
from .weather import WeatherSupport

_logger = logging.getLogger("moreopenrepeater.service")


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
        activity: Optional[ActivityRecorder] = None,
    ) -> None:
        """`clock` is monotonic and drives the controller's timers;
        `wall_clock` is naive local time and drives scheduled announcements."""
        self._clock = clock
        self._wall_clock = wall_clock
        self._link_command_sink = link_command_sink
        self._state_store = state_store
        self.renderer = renderer
        self.activity = activity
        self.audio_output: Optional[AudioOutput] = None
        self.lockout_hook: Optional[Callable[[bool], None]] = None  # stuck-carrier lockout engaged/cleared
        self.net_active = False
        saved = state_store.load() if state_store is not None else None
        if saved is not None:
            config = config_from_snapshot(saved)
            macros = macros_from_snapshot(saved)
            announcements = announcements_from_snapshot(saved)
            _logger.info("loaded saved state from %s", state_store.path)
        saved_config = config or RepeaterConfig()
        self.controller = RepeaterController(
            effective_config(saved_config),
            macros=macros or [],
            now=clock(),
            clip_duration=self._clip_duration,
        )
        self._config = ConfigPersistence(self, saved_config)
        self._weather = WeatherSupport(self)
        self._links = LinkSupport(self)
        self._patch = PatchSupport(self)
        self._programming = Programming(self, AnnouncementScheduler(announcements or [], wall_clock()))
        self.ptt_active = False
        self.cos_active = False
        self.ctcss_hz: Optional[float] = None
        self.last_clip: Optional[str] = None
        self._last_state = self.controller.state
        self._repeating = False
        self._kerchunks_filtered = 0
        self._locked_out = False
        self._subscribers: set["asyncio.Queue[StatusSnapshot]"] = set()

    # --- collaborators (public so hooks can move onto them over time) ---

    @property
    def programming(self) -> Programming:
        return self._programming

    @property
    def weather(self) -> WeatherSupport:
        return self._weather

    @property
    def patch(self) -> PatchSupport:
        return self._patch

    @property
    def config_persist(self) -> ConfigPersistence:
        return self._config

    @property
    def links(self) -> LinkSupport:
        return self._links

    # --- state mirrors kept flat for existing call sites ---

    @property
    def scheduler(self) -> AnnouncementScheduler:
        return self._programming.scheduler

    @property
    def weather_tracker(self):
        return self._weather.tracker

    @property
    def weather_alerts(self) -> list[WeatherAlert]:
        return self._weather.alerts

    @weather_alerts.setter
    def weather_alerts(self, value: list[WeatherAlert]) -> None:
        self._weather.alerts = value

    @property
    def weather_last_checked(self) -> Optional[datetime]:
        return self._weather.last_checked

    @weather_last_checked.setter
    def weather_last_checked(self, value: Optional[datetime]) -> None:
        self._weather.last_checked = value

    @property
    def weather_last_error(self) -> Optional[str]:
        return self._weather.last_error

    @weather_last_error.setter
    def weather_last_error(self, value: Optional[str]) -> None:
        self._weather.last_error = value

    @property
    def linked_nodes(self) -> set[str]:
        return self._links.nodes

    @linked_nodes.setter
    def linked_nodes(self, value: set[str]) -> None:
        self._links.nodes = value

    @property
    def link_audio(self) -> bool:
        return self._links.audio

    @link_audio.setter
    def link_audio(self, value: bool) -> None:
        self._links.audio = value

    # --- hooks forwarded onto the cluster that invokes them ---

    @property
    def autopatch(self) -> Optional[PatchCalls]:
        return self._patch.calls

    @autopatch.setter
    def autopatch(self, value: Optional[PatchCalls]) -> None:
        self._patch.calls = value

    @property
    def audit_hook(self) -> Optional[Callable[[str, str, str], None]]:
        return self._programming.audit_hook

    @audit_hook.setter
    def audit_hook(self, value: Optional[Callable[[str, str, str], None]]) -> None:
        self._programming.audit_hook = value

    @property
    def aprs_summary(self) -> Optional[Callable[[], str]]:
        return self._programming.aprs_summary

    @aprs_summary.setter
    def aprs_summary(self, value: Optional[Callable[[], str]]) -> None:
        self._programming.aprs_summary = value

    @property
    def gpio_command(self) -> Optional[Callable[[str], str]]:
        return self._programming.gpio_command

    @gpio_command.setter
    def gpio_command(self, value: Optional[Callable[[str], str]]) -> None:
        self._programming.gpio_command = value

    @property
    def net_hook(self) -> Optional[Callable[[str, str], None]]:
        return self._programming.net_hook

    @net_hook.setter
    def net_hook(self, value: Optional[Callable[[str, str], None]]) -> None:
        self._programming.net_hook = value

    @property
    def code_checker(self) -> Optional[Callable[[str], Optional[str]]]:
        return self._programming.code_checker

    @code_checker.setter
    def code_checker(self, value: Optional[Callable[[str], Optional[str]]]) -> None:
        self._programming.code_checker = value

    @property
    def codes_locked(self) -> Callable[[], bool]:
        return self._programming.codes_locked

    @codes_locked.setter
    def codes_locked(self, value: Callable[[], bool]) -> None:
        self._programming.codes_locked = value

    @property
    def homeassistant_hook(self) -> Optional[Callable[[str, str, str], None]]:
        return self._programming.homeassistant_hook

    @homeassistant_hook.setter
    def homeassistant_hook(self, value: Optional[Callable[[str, str, str], None]]) -> None:
        self._programming.homeassistant_hook = value

    @property
    def metar_hook(self) -> Optional[Callable[[str], None]]:
        return self._programming.metar_hook

    @metar_hook.setter
    def metar_hook(self, value: Optional[Callable[[str], None]]) -> None:
        self._programming.metar_hook = value

    @property
    def sensor_hook(self) -> Optional[Callable[[str], None]]:
        return self._programming.sensor_hook

    @sensor_hook.setter
    def sensor_hook(self, value: Optional[Callable[[str], None]]) -> None:
        self._programming.sensor_hook = value

    @property
    def remote_base_hook(self) -> Optional[Callable[[str, str], None]]:
        return self._programming.remote_base_hook

    @remote_base_hook.setter
    def remote_base_hook(self, value: Optional[Callable[[str, str], None]]) -> None:
        self._programming.remote_base_hook = value

    @property
    def mailbox_hook(self) -> Optional[Callable[[MailboxCommand], None]]:
        return self._programming.mailbox_hook

    @mailbox_hook.setter
    def mailbox_hook(self, value: Optional[Callable[[MailboxCommand], None]]) -> None:
        self._programming.mailbox_hook = value

    # --- config / mode ---

    @property
    def config(self) -> RepeaterConfig:
        """The settings in force: the saved ones with net mode and GMRS mode applied."""
        return self.controller.config

    @property
    def saved_config(self) -> RepeaterConfig:
        """The settings as the user saved them, without any mode's changes."""
        return self._config.saved

    def held_reason(self, feature: str) -> str:
        """Why `feature` ("autopatch", "links", "aprs") is off right now although it's switched on, else ""."""
        return held_reason(self._config.saved, self.net_active, feature)

    def set_net_active(self, active: bool) -> None:
        if active == self.net_active:
            return
        _logger.info("net mode %s", "on" if active else "off")
        self.net_active = active
        self._config.apply(self._config.saved)

    def idle_seconds(self) -> float:
        """How long nobody, local or linked, has used the repeater; 0 while
        it's transmitting, on a call, or running a net."""
        if self.net_active or self.controller.state != IDLE:
            return 0.0
        return self.controller.quiet_for(self._clock())

    def wall_now(self) -> datetime:
        return self._wall_clock()

    @property
    def repeating(self) -> bool:
        """Whether received audio should go out the transmitter: normally
        while RECEIVING, and during an autopatch call whenever a user talks."""
        state = self.controller.state
        return state == RECEIVING or (state == PATCH and self.controller.carrier_present)

    def set_link_command_sink(self, sink: Callable[[SendLinkCommand], None]) -> None:
        self._link_command_sink = sink

    def add_config_listener(self, listener: Callable[[RepeaterConfig], None]) -> None:
        self._config.add_listener(listener)

    def _clip_duration(self, clip: str) -> Optional[float]:
        if self.renderer is None:
            return None
        return self.renderer.cached_duration(clip, self.controller.config)

    # --- status pubsub ---

    def snapshot(self) -> StatusSnapshot:
        return StatusSnapshot(
            state=self.controller.state,
            ptt_active=self.ptt_active,
            cos_active=self.cos_active,
            ctcss_hz=self.ctcss_hz,
            linked_nodes=sorted(self._links.nodes),
            last_clip=self.last_clip,
            timestamp=self._clock(),
            transmitter_enabled=self.controller.config.transmitter_enabled,
            locked_out=self.controller.locked_out,
            net_active=self.net_active,
            gmrs_mode=self._config.saved.gmrs_mode,
            callsign=self._config.saved.callsign,
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

    # --- controller command loop (orchestration) ---

    def _apply_commands(self, commands: list[ControllerCommand]) -> None:
        now = self._wall_clock().timestamp()
        actions: list[Union[RunAction, CodedCommand, DialPatch, HangupPatch, MailboxCommand]] = []
        for command in commands:
            if isinstance(command, AssertPTT):
                if self.activity is not None and command.active != self.ptt_active:
                    self.activity.ptt_changed(command.active, now)
                self.ptt_active = command.active
                if self.audio_output is not None:
                    self.audio_output.set_ptt(command.active)
            elif isinstance(command, PlayAudio):
                self.last_clip = command.clip
                if self.activity is not None:
                    self.activity.clip_played(command.clip, now)
                if self.audio_output is not None:
                    self.audio_output.play(command.clip)
            elif isinstance(command, SendLinkCommand):
                if self._config.saved.gmrs_mode:
                    _logger.warning("link command %r ignored: linking is off in GMRS mode", command.command)
                else:
                    self._link_command_sink(command)
            elif isinstance(command, (RunAction, CodedCommand, DialPatch, HangupPatch, MailboxCommand)):
                actions.append(command)
        filtered = self.controller.kerchunks_filtered
        if filtered > self._kerchunks_filtered and self.activity is not None:
            self.activity.kerchunk_filtered(now)
        self._kerchunks_filtered = filtered
        locked_out = self.controller.locked_out
        if locked_out != self._locked_out:
            self._locked_out = locked_out
            if locked_out and self.activity is not None:
                self.activity.locked_out(now)
            if self.lockout_hook is not None:
                self.lockout_hook(locked_out)
        state = self.controller.state
        if state != self._last_state:
            if self.activity is not None:
                self.activity.state_changed(self._last_state, state, now)
            self._last_state = state
        repeating = self.repeating
        if repeating != self._repeating:
            self._repeating = repeating
            if self.audio_output is not None:
                self.audio_output.set_repeating(repeating)
        self._notify()
        # After the state bookkeeping above, since actions can change config
        # (and so re-enter this method).
        for action in actions:
            if isinstance(action, RunAction):
                self._run_action(action)
            elif isinstance(action, CodedCommand):
                self._run_coded(action)
            elif isinstance(action, MailboxCommand):
                self._programming.run_mailbox(action)
            else:
                self._patch.run_command(action)

    def _run_coded(self, coded: CodedCommand) -> None:
        self._programming.run_coded(coded)

    def _run_patch_command(self, command: Union[DialPatch, HangupPatch]) -> None:
        self._patch.run_command(command)

    def begin_patch(self) -> None:
        self._patch.begin()

    def end_patch(self) -> None:
        self._patch.end()

    def clear_lockout(self) -> None:
        self._apply_commands(self.controller.clear_lockout(self._clock()))

    def run_macro(self, pattern: str, source: str) -> bool:
        return self._programming.run_macro(pattern, source)

    def _run_action(self, action: RunAction) -> None:
        self._programming.run_action(action)

    def speak(self, clip: str) -> None:
        self._programming.speak(clip)

    def weather_summary_text(self) -> str:
        return self._weather.summary_text()

    def handle_audio_events(self, events: list[ControllerEvent]) -> None:
        """Carrier / CTCSS / DTMF detected by the live audio engine."""
        commands: list[ControllerCommand] = []
        for event in events:
            if isinstance(event, COSChanged):
                self.cos_active = event.active
            elif isinstance(event, CTCSSChanged):
                self.ctcss_hz = event.tone_hz
            elif isinstance(event, DTMFDigit):
                _logger.info("DTMF digit %r received", event.digit)
                self._pass_digit_to_call(event.digit)
            commands += self.controller.handle_event(event, self._clock())
        self._apply_commands(commands)

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
        self._pass_digit_to_call(digit)
        self._apply_commands(self.controller.handle_event(DTMFDigit(digit=digit), self._clock()))

    def _pass_digit_to_call(self, digit: str) -> None:
        self._patch.pass_digit(digit)

    def simulate_remote_keyed(self, node_id: str, keyed: bool) -> None:
        self._links.simulate_remote_keyed(node_id, keyed)

    def handle_link_event(self, event: ControllerEvent) -> None:
        self._links.handle_event(event)

    def link_radio_keyed(self, keyed: bool) -> None:
        self._links.radio_keyed(keyed)

    def update_config(self, **overrides: object) -> RepeaterConfig:
        return self._config.update(**overrides)

    def _apply_config(self, saved: RepeaterConfig, persist: bool = False) -> None:
        self._config.apply(saved, persist=persist)

    def list_macros(self) -> list[Macro]:
        return self._programming.list_macros()

    def add_macro(self, macro: Macro) -> list[Macro]:
        return self._programming.add_macro(macro)

    def delete_macro(self, pattern: str) -> list[Macro]:
        return self._programming.delete_macro(pattern)

    def list_announcements(self) -> list[Announcement]:
        return self._programming.list_announcements()

    def next_announcement_run(self, announcement_id: str) -> Optional[datetime]:
        return self._programming.next_announcement_run(announcement_id)

    def save_announcement(self, announcement: Announcement) -> Announcement:
        return self._programming.save_announcement(announcement)

    def delete_announcement(self, announcement_id: str) -> None:
        self._programming.delete_announcement(announcement_id)

    def announcement_clip(self, announcement: Announcement) -> str:
        return self._programming.announcement_clip(announcement)

    def due_announcement_clips(self) -> list[str]:
        return self._programming.due_announcement_clips()

    def queue_announcement(self, clip: str) -> bool:
        return self._programming.queue_announcement(clip)

    def weather_polled(self, geojson: dict, announce: bool = True) -> list[str]:
        return self._weather.polled(geojson, announce=announce)

    def weather_poll_failed(self, error: str) -> None:
        self._weather.poll_failed(error)

    def weather_alert_clip(self, alert: WeatherAlert) -> str:
        return self._weather.alert_clip(alert)

    def export_snapshot(self) -> dict:
        return self._config.export_snapshot()

    def import_snapshot(self, data: dict) -> None:
        self._config.import_snapshot(data)

    def _config_changed(self) -> None:
        self._config.changed()

    def _persist(self) -> None:
        self._config.persist()
