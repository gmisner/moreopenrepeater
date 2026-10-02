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
from typing import Callable, Optional, Protocol, Union

import numpy as np

from controller.announcements import Announcement, AnnouncementScheduler, announcement_from_dict
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
    LinkStateChanged,
    MailboxCommand,
    PlayAudio,
    RemoteKeyed,
    RunAction,
    SendLinkCommand,
)
from controller.macros import Macro
from controller.modes import effective_config, held_reason
from controller.state_machine import IDLE, PATCH, RECEIVING, RepeaterConfig, RepeaterController
from audio_io.patch import LinkAudio, PatchAudio

from playout.renderer import ASSET_PREFIX, TTS_PREFIX, ClipRenderer
from wx.nws import AlertTracker, WeatherAlert, meets_severity, parse_alerts, speech_text

from .activity import ActivityRecorder
from .persistence import StateStore

_logger = logging.getLogger("moreopenrepeater.service")

LINK_AUDIO_NODE = "allstar"  # RemoteKeyed's node_id for the AllStar node's own transmitter

WEATHER_SUMMARY_MAX = 3
MAX_HELD_ANNOUNCEMENTS = 10


def spoken_time(now: datetime) -> str:
    hour = now.hour % 12 or 12
    minutes = "o'clock" if now.minute == 0 else f"{now.minute:02d}" if now.minute >= 10 else f"oh {now.minute}"
    return f"{hour} {minutes} {'A M' if now.hour < 12 else 'P M'}"


def talking_clock_text(now: datetime) -> str:
    return f"The time is {spoken_time(now)}."

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
    config = dict(data.get("config", {}))
    if "public_page_enabled" in config:  # saved before public_page_mode
        config.setdefault("public_page_mode", "anyone" if config.pop("public_page_enabled") else "off")
    return RepeaterConfig(**_known_fields(config, _CONFIG_FIELDS, "config"))


def macros_from_snapshot(data: dict) -> list[Macro]:
    return [Macro(**_known_fields(m, _MACRO_FIELDS, "macro")) for m in data.get("macros", [])]


def announcements_from_snapshot(data: dict) -> list[Announcement]:
    return [announcement_from_dict(a) for a in data.get("announcements", [])]


class AudioOutput(Protocol):
    """What the service drives when a live audio engine is attached."""

    def set_ptt(self, active: bool) -> None: ...
    def set_repeating(self, repeating: bool) -> None: ...
    def play(self, clip: str) -> None: ...
    def arm_parrot(self) -> bool: ...
    def arm_capture(self, max_seconds: float, on_done: Callable[[np.ndarray, float], None]) -> bool: ...
    def set_patch(self, patch: Optional[PatchAudio]) -> None: ...
    def set_link(self, link: Optional[LinkAudio]) -> None: ...


class PatchCalls(Protocol):
    """Places and ends autopatch calls (`api.autopatch.Autopatch`)."""

    def dial(self, number: str, actor: str) -> Optional[str]: ...
    def hangup(self, reason: str) -> None: ...


@dataclasses.dataclass(frozen=True)
class StatusSnapshot:
    state: str
    ptt_active: bool
    cos_active: bool
    ctcss_hz: Optional[float]
    linked_nodes: list[str]
    last_clip: Optional[str]
    timestamp: float
    transmitter_enabled: bool = True
    locked_out: bool = False
    net_active: bool = False
    gmrs_mode: bool = False


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
        self.autopatch: Optional[PatchCalls] = None
        self.audit_hook: Optional[Callable[[str, str, str], None]] = None  # (actor, action, detail)
        self.aprs_summary: Optional[Callable[[], str]] = None
        self.gpio_command: Optional[Callable[[str], str]] = None  # runs a gpio macro, returns what to say
        self.lockout_hook: Optional[Callable[[bool], None]] = None  # the stuck-carrier lockout engaged/cleared
        self.net_hook: Optional[Callable[[str, str], None]] = None  # ("net_start" | "net_end", who asked)
        self.code_checker: Optional[Callable[[str], Optional[str]]] = None  # one-time code -> whose it is (api.control_codes)
        self.codes_locked: Callable[[], bool] = lambda: False
        self.homeassistant_hook: Optional[Callable[[str, str, str], None]] = None  # (target, source, pattern)
        self.mailbox_hook: Optional[Callable[[MailboxCommand], None]] = None  # api.mailbox
        self._action_source = "DTMF"
        self._config_listeners: list[Callable[[RepeaterConfig], None]] = []
        saved = state_store.load() if state_store is not None else None
        if saved is not None:
            config = config_from_snapshot(saved)
            macros = macros_from_snapshot(saved)
            announcements = announcements_from_snapshot(saved)
            _logger.info("loaded saved state from %s", state_store.path)
        self._saved_config = config or RepeaterConfig()
        self.net_active = False
        self._held_announcements: list[str] = []
        self.controller = RepeaterController(
            effective_config(self._saved_config), macros=macros or [], now=clock(), clip_duration=self._clip_duration
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
        self.link_audio = False  # api.allstar_audio is carrying the node's audio
        self.last_clip: Optional[str] = None
        self._last_state = self.controller.state
        self._repeating = False
        self._kerchunks_filtered = 0
        self._locked_out = False
        self._subscribers: set["asyncio.Queue[StatusSnapshot]"] = set()

    @property
    def config(self) -> RepeaterConfig:
        """The settings in force: the saved ones with net mode and GMRS mode applied."""
        return self.controller.config

    @property
    def saved_config(self) -> RepeaterConfig:
        """The settings as the user saved them, without any mode's changes."""
        return self._saved_config

    def held_reason(self, feature: str) -> str:
        """Why `feature` ("autopatch", "links", "aprs") is off right now although it's switched on, else ""."""
        return held_reason(self._saved_config, self.net_active, feature)

    def set_net_active(self, active: bool) -> None:
        if active == self.net_active:
            return
        _logger.info("net mode %s", "on" if active else "off")
        self.net_active = active
        self._apply_config(self._saved_config)

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
            transmitter_enabled=self.controller.config.transmitter_enabled,
            locked_out=self.controller.locked_out,
            net_active=self.net_active,
            gmrs_mode=self._saved_config.gmrs_mode,
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
                if self._saved_config.gmrs_mode:
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
                if self.mailbox_hook is None:
                    self.speak(TTS_PREFIX + "The mailbox is not available.")
                else:
                    self.mailbox_hook(action)
            else:
                self._run_patch_command(action)

    def _run_coded(self, coded: CodedCommand) -> None:
        user = self.code_checker(coded.code) if self.code_checker is not None else None
        if user is None:
            locked = self.codes_locked()
            _logger.warning("macro %s: one-time code rejected%s", coded.pattern, " (locked out)" if locked else "")
            if self.audit_hook is not None:
                self.audit_hook("DTMF", "DTMF code rejected", coded.pattern)
            self.speak(TTS_PREFIX + ("Codes are locked out. Try again later." if locked else "Code rejected."))
            return
        source = f"DTMF ({user})"
        if isinstance(coded.command, SendLinkCommand) and self.audit_hook is not None:
            self.audit_hook(source, f"{source} link", coded.command.command)
        self._action_source = source
        try:
            self._apply_commands([coded.command])
        finally:
            self._action_source = "DTMF"

    def _run_patch_command(self, command: Union[DialPatch, HangupPatch]) -> None:
        if isinstance(command, HangupPatch):
            if self.autopatch is not None:
                self.autopatch.hangup("hung up by a user")
            return
        _logger.info("autopatch dial requested over DTMF")
        if self.autopatch is None:
            self.speak(TTS_PREFIX + "Autopatch is not available.")
            return
        self.autopatch.dial(command.number, "DTMF")

    def begin_patch(self) -> None:
        """An autopatch call started: hold the transmitter up for it."""
        self.controller.set_patch_call_active(True)
        self._apply_commands(self.controller.start_patch(self._clock()))

    def end_patch(self) -> None:
        self.controller.set_patch_call_active(False)
        self._apply_commands(self.controller.end_patch(self._clock()))

    def clear_lockout(self) -> None:
        self._apply_commands(self.controller.clear_lockout(self._clock()))

    def run_macro(self, pattern: str, source: str) -> bool:
        """Run a saved macro as if its code had been dialed; `source` names what ran it."""
        macro = next((m for m in self.list_macros() if m.pattern == pattern), None)
        if macro is None:
            _logger.warning("%s: there's no macro %r", source, pattern)
            return False
        self._action_source = source
        try:
            self._apply_commands([macro.build_command()])
        finally:
            self._action_source = "DTMF"
        return True

    def _run_action(self, action: RunAction) -> None:
        source = self._action_source
        _logger.info("%s action %s(%r)", source, action.action, action.argument)
        if self.audit_hook is not None:
            self.audit_hook(source, f"{source} {action.action}", action.argument)
        if action.action == "tx_disable":
            self.update_config(transmitter_enabled=False)
        elif action.action == "tx_enable":
            self.update_config(transmitter_enabled=True)
            self.speak(TTS_PREFIX + "Transmitter enabled")
        elif action.action == "time":
            self.speak(TTS_PREFIX + talking_clock_text(self._wall_clock()))
        elif action.action == "weather":
            self.speak(TTS_PREFIX + self.weather_summary_text())
        elif action.action == "id":
            self.speak("id")
        elif action.action == "say" and action.argument:
            self.speak(TTS_PREFIX + action.argument)
        elif action.action == "announcement":
            announcement = next((a for a in self.scheduler.list() if a.id == action.argument), None)
            if announcement is None:
                _logger.warning("DTMF macro refers to unknown announcement %r", action.argument)
            else:
                self.speak(self.announcement_clip(announcement))
        elif action.action == "aprs":
            text = self.aprs_summary() if self.aprs_summary else "The A P R S map is not set up."
            self.speak(TTS_PREFIX + text)
        elif action.action == "gpio":
            text = self.gpio_command(action.argument) if self.gpio_command else "That output is not set up."
            self.speak(TTS_PREFIX + text)
        elif action.action == "lockout_clear":
            if self.controller.locked_out:
                self.clear_lockout()
                self.speak(TTS_PREFIX + "Lockout cleared")
        elif action.action in ("net_start", "net_end"):
            if self.net_hook is None:
                _logger.warning("net mode isn't available")
            else:
                self.net_hook(action.action, source)
        elif action.action == "homeassistant":
            if self.homeassistant_hook is None:
                self.speak(TTS_PREFIX + "Home Assistant is not set up.")
            else:
                self.homeassistant_hook(action.argument, source, action.pattern)
        elif action.action == "parrot":
            if self.audio_output is None:
                _logger.warning("parrot needs live audio, which isn't running")
            elif self.audio_output.arm_parrot():
                self.speak(TTS_PREFIX + "Parrot ready. Key up and speak.")
        else:
            _logger.warning("DTMF action %s(%r) did nothing", action.action, action.argument)

    def speak(self, clip: str) -> None:
        """Queue `clip` like an announcement, rendering it off the event loop
        first so the controller knows how long to hold PTT."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if self.renderer is None or loop is None:
            self.queue_announcement(clip)
            return
        config = self.controller.config

        def rendered(future: "asyncio.Future") -> None:
            if future.exception() is not None:
                _logger.error("couldn't render %s: %s", clip, future.exception())
            elif not self.queue_announcement(clip):
                _logger.warning("announcement queue full; dropped %s", clip)

        loop.run_in_executor(None, self.renderer.render, clip, config).add_done_callback(rendered)

    def weather_summary_text(self) -> str:
        config = self.controller.config
        if config.wx_lat is None or config.wx_lon is None:
            return "Weather alerts are not set up."
        if not self.weather_alerts:
            return "There are no active weather alerts."
        count = len(self.weather_alerts)
        intro = "There is one active weather alert." if count == 1 else f"There are {count} active weather alerts."
        return " ".join([intro] + [speech_text(a) for a in self.weather_alerts[:WEATHER_SUMMARY_MAX]])

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
        if isinstance(event, RemoteKeyed) and self.link_audio and event.node_id != LINK_AUDIO_NODE:
            # The node's own key-up (with its audio) says when to transmit;
            # a linked station can be keyed without the node sending it.
            return
        if isinstance(event, LinkStateChanged):
            if event.linked:
                self.linked_nodes.add(event.node_id)
            else:
                self.linked_nodes.discard(event.node_id)
        self._apply_commands(self.controller.handle_event(event, self._clock()))

    def update_config(self, **overrides: object) -> RepeaterConfig:
        """Change saved settings; returns the new saved settings."""
        _logger.info("update_config(%s)", overrides)
        self._apply_config(dataclasses.replace(self._saved_config, **overrides), persist=True)
        return self._saved_config

    def _apply_config(self, saved: RepeaterConfig, persist: bool = False) -> None:
        self._saved_config = saved
        commands = self.controller.update_config(effective_config(saved, self.net_active), self._clock())
        if persist:
            self._persist()
        self._config_changed()
        self._apply_commands(commands)

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
        """Scheduled announcements to play now. During a net they wait
        (if net_hold_announcements), and play once it's over."""
        due = self.scheduler.due(self._wall_clock())
        for announcement in due:
            _logger.info("announcement %r is due", announcement.name)
        clips = [self.announcement_clip(a) for a in due]
        if self.net_active and self._saved_config.net_hold_announcements:
            self._held_announcements = (self._held_announcements + clips)[-MAX_HELD_ANNOUNCEMENTS:]
            return []
        held, self._held_announcements = self._held_announcements, []
        return held + clips

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
            "config": dataclasses.asdict(self._saved_config),
            "macros": [dataclasses.asdict(m) for m in self.controller.list_macros()],
            "announcements": [dataclasses.asdict(a) for a in self.scheduler.list()],
        }

    def import_snapshot(self, data: dict) -> None:
        """Replace everything wholesale. Fields missing from `data` (e.g. a
        backup taken before a setting existed) get their defaults."""
        _logger.info("import_snapshot()")
        self._saved_config = config_from_snapshot(data)
        self.controller.update_config(effective_config(self._saved_config, self.net_active), self._clock())
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
