"""The API's view of a core that runs in its own process (Stage 3 slices 7c-2 and 7c-3b, #229).

`RemoteCore` follows the core's `GET /events` stream over its control socket and
keeps the latest status and the saved configuration in memory. Reading it never
touches the socket, so it is instant and can be called from any route. It never
starts or stops anything. The one thing it can do is `change_config`: it asks the
core to change settings, waits for the core's own answer, and says so plainly if the
core refuses or cannot be reached. It never claims a change that the core did not
confirm.

The core does not depend on this in any way. If the core is down, slow or
restarting, `RemoteCore` says so (`connected`, `stale`, `age_s`) and keeps trying
to reconnect, and the API carries on.

It imports no web framework.
"""
from __future__ import annotations

import asyncio
import collections
import dataclasses
import types
import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import httpx

from contracts import (
    CommandResult,
    CommandStatus,
    ConfigApplied,
    ConfigChange,
    ConfigResult,
    ConfigState,
    CoreCommand,
    CoreHardware,
    CoreLogs,
    CoreStatus,
    Header,
    LinkEvents,
    CommandName,
    PROGRAMMING,
    ProgrammingState,
    Sequencer,
    Source,
    new_id,
)

from controller.announcements import Announcement
from controller.events import ControllerEvent, LinkStateChanged, MailboxCommand, RemoteKeyed
from controller.config import RepeaterConfig
from controller.macros import Macro
from controller.modes import held_reason as _held_reason
from controller.modes import effective_config

from .core_runtime import AnnouncementQueueFull
from .remote_audio import RemoteAudioMonitor
from .remote_activity import RemoteActivityStore
from .service.core_handle import CommandRefused, ConfigRefused, CoreUnavailable
from playout.renderer import ASSET_PREFIX, TTS_PREFIX, UnknownClipError
from playout.tts import TTSError
from playout.wav import WavError, decode_wav

from .service.snapshot import StatusSnapshot
from .service.weather import WeatherSupport

_logger = logging.getLogger("moreopenrepeater.remote_core")

#: No event of any kind (status or heartbeat) for this long means the stream is dead.
#: The core sends a heartbeat every 5 s.
STALE_AFTER_SECONDS = 15.0
CONNECT_TIMEOUT_SECONDS = 2.0
RETRY_DELAYS = (0.5, 1.0, 2.0, 5.0)  # then the last one, forever
HARDWARE_POLL_SECONDS = 2.0  # how often the core's hardware status is read
HARDWARE_STALE_SECONDS = 10.0  # older than this, a hardware page says the core is not being heard
LOG_POLL_SECONDS = 1.0  # how often the core's log is read when nothing is waiting
MAX_LINE_BYTES = 64 * 1024
REQUEST_TIMEOUT_SECONDS = 5.0  # a write must not hang the dashboard
MAX_DEFERRED = 20  # announcements held for a net; a long net does not grow this without bound
MAX_LINK_OUTBOX = 200  # link events waiting to go to the core; past this a whole picture replaces them
MAX_CONFIG_BYTES = 1024 * 1024  # what we will read back from the core
MAX_CONFIG_READS = 5  # reads in a row while the core keeps announcing something newer

#: What a snapshot says while nothing is known. The state is not one the controller
#: has, so a dashboard cannot mistake it for the radio's state.
UNAVAILABLE_STATE = "core_unavailable"


class RemoteCore:
    def __init__(self, socket_path: Path, *, clock=time.monotonic) -> None:
        self.socket_path = socket_path
        self._clock = clock
        self._snapshot: Optional[StatusSnapshot] = None
        self._snapshot_at: float = 0.0  # when `_snapshot` arrived, on `clock`
        self._started_at: float = clock()
        self._seen_before = False  # a first status is not a change
        self._hardware: dict[str, dict] = {}
        self._hardware_at: Optional[float] = None
        self._hardware_task: Optional[asyncio.Task] = None
        self._log_hub = None  # where the core's log lines are published (the dashboard's Logs page)
        self._log_task: Optional[asyncio.Task] = None
        self._log_cursor = 0
        self._log_boot: Optional[str] = None
        # httpx logs every request at INFO; reading the core's log once a second would fill the log.
        logging.getLogger("httpx").setLevel(logging.WARNING)
        self._status: Optional[CoreStatus] = None
        self._health: Optional[str] = None  # from the latest heartbeat
        self._connected = False
        self._last_event_at: Optional[float] = None
        self._subscribers: set[asyncio.Queue] = set()
        self._task: Optional[asyncio.Task] = None
        self._stopping = False
        self.reconnects = 0
        self._config: Optional[RepeaterConfig] = None  # the core's saved configuration
        self._config_version: Optional[int] = None  # the version `_config` is at
        self._announced_version: Optional[int] = None  # the newest version the core has told us of
        self._fetching: Optional[asyncio.Task] = None
        self._read_again = False  # a refresh was asked for while a read was running
        self._macros: Optional[list[Macro]] = None
        self._announcements: list[Announcement] = []
        self._next_runs: dict[str, Optional[datetime]] = {}
        self._speaking: set[asyncio.Task] = set()  # fire-and-forget speak() calls in flight
        self._macro_handlers: dict[str, Callable] = {}  # macro family -> what to call
        self._audio_monitors: dict[str, RemoteAudioMonitor] = {}
        #: What to call with the next recorded transmission, if the mailbox is waiting for one.
        self._capture_waiting: Optional[Callable] = None
        self._macro_handlers["capture"] = self._captured  # the core names the recording in a `capture` event
        self.audio_output = _RemoteCapture(self)
        self._listeners: list[Callable[[RepeaterConfig], None]] = []
        self._deferred: collections.deque = collections.deque(maxlen=MAX_DEFERRED)  # (clip, on_linked_radio) held for a net
        self._net_hint: Optional[bool] = None  # what the API's saved net state says; resent on every connect
        #: The AllStar link service's picture of the links, {node: keyed}, once it has given one. A restarted
        #: core forgets it, so it is sent again after every connect.
        self._link_state: Optional[dict[str, bool]] = None
        #: The core has been, or is about to be, given everything in `_link_state`, and what follows goes after it in
        #: order. False at the start, after a failure and after a restart: the next thing sent is the whole picture.
        self._link_in_sync = False
        self._link_outbox: collections.deque = collections.deque()
        self._link_wake: Optional[asyncio.Event] = None
        self._link_worker: Optional[asyncio.Task] = None
        self._busy_at: Optional[float] = None  # the last time the repeater was seen in use
        # Things the in-process service has and the API's composition sets. The core owns the real ones;
        # setting them here is accepted and does nothing, so one composition works in both modes.
        self.renderer = None
        self.activity = None
        self.link_radio_play = None
        self.lockout_hook: Optional[Callable[[bool], None]] = None
        self.programming = types.SimpleNamespace(
            code_checker=None, codes_locked=lambda: False, audit_hook=None, net_hook=None,
            gpio_command=None, aprs_summary=None, homeassistant_hook=None, metar_hook=None,
            sensor_hook=None, remote_base_hook=None, mailbox_hook=None,
        )
        # NWS alert state is the API's (it does the polling); the core only plays what it is told.
        self.weather = WeatherSupport(self)  # type: ignore[arg-type]
        self._sequencer = Sequencer()

    #: The API's composition checks this to run without the core's own parts (audio, PTT, GPIO).
    is_remote = True

    # -- lifecycle ------------------------------------------------------------

    def register_macro_handler(self, family: str, handler: Callable) -> None:
        """Take a macro action the API's modules own (`net`, `homeassistant`, `metar`, `sensor`,
        `mailbox`, `aprs` or `weather`). `handler` is called with the same arguments the in-process hook
        gets (`aprs` and `weather` return the sentence to speak). The core delivers these actions only to a client that
        said it handles them when it connected, so register before `start()`."""
        if self._task is not None:
            raise RuntimeError("register macro handlers before start(): the core is told what we handle when we connect")
        self._macro_handlers[family] = handler

    def start(self) -> None:
        """Start following the core. Never raises, and does not wait: the core may not be up yet."""
        if self._task is None:
            self._stopping = False
            self._task = asyncio.ensure_future(self._run())
        if self._hardware_task is None:
            self._hardware_task = asyncio.ensure_future(self._pull_hardware())
        if self._log_hub is not None and self._log_task is None:
            self._log_task = asyncio.ensure_future(self._pull_logs())

    async def stop(self) -> None:
        # The flag matters: on Python 3.11 a cancel that lands inside the HTTP client while
        # we are retrying a dead socket can be swallowed, and then only the flag ends the loop.
        self._stopping = True
        for pending in list(self._speaking):
            pending.cancel()
        for monitor in self._audio_monitors.values():
            monitor.close()
        for name in ("_task", "_fetching", "_log_task", "_hardware_task", "_link_worker"):
            task, current = getattr(self, name), None
            setattr(self, name, current)
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._connected = False

    # -- what the API reads -------------------------------------------------------

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def age_s(self) -> Optional[float]:
        """Seconds since anything arrived from the core, or None if nothing ever has."""
        return None if self._last_event_at is None else max(0.0, self._clock() - self._last_event_at)

    @property
    def stale(self) -> bool:
        """True unless the core is connected and has been heard from recently."""
        age = self.age_s
        return not self._connected or age is None or age > STALE_AFTER_SECONDS

    @property
    def core_health(self) -> Optional[str]:
        """The core's own view of its health (`ok` or `failed`), from its last heartbeat."""
        return self._health

    @property
    def status(self) -> Optional[CoreStatus]:
        """The last `core.status` contract message received."""
        return self._status

    @property
    def locked_out(self) -> bool:
        return bool(self._snapshot and self._snapshot.locked_out)

    @property
    def linked_nodes(self) -> set[str]:
        return set(self._snapshot.linked_nodes) if self._snapshot else set()

    @property
    def saved_config(self) -> RepeaterConfig:
        """The core's saved configuration, as last read. Raises `CoreUnavailable` if the
        core has never been heard from, so a page cannot show made-up defaults."""
        if self._config is None:
            raise CoreUnavailable("the core has not been reached yet, so its configuration is not known")
        return self._config

    @property
    def config(self) -> RepeaterConfig:
        """The settings in force: the saved ones with net mode applied, as the controller runs them."""
        net_active = bool(self._snapshot and self._snapshot.net_active)
        return effective_config(self.saved_config, net_active)

    # -- macros and announcements, read from the cache ----------------------------------------

    def list_macros(self) -> list[Macro]:
        if self._macros is None:
            raise CoreUnavailable("the core has not been reached yet, so its macros are not known")
        return list(self._macros)

    def export_snapshot(self) -> dict:
        """The core's settings, macros and announcements in the shape a backup stores. Built
        from what the core last reported, which it re-reports as soon as anything changes. It
        raises `CoreUnavailable` if the core is not being heard from, so a backup is never
        taken from an old copy."""
        if self._config is None or self._macros is None or self.stale:
            raise CoreUnavailable("the core is not being heard from, so its settings cannot be exported now")
        return {
            "config": self._config.to_flat_dict(),
            "macros": [dataclasses.asdict(m) for m in self._macros],
            "announcements": [dataclasses.asdict(a) for a in self._announcements],
        }

    def list_announcements(self) -> list[Announcement]:
        if self._macros is None:
            raise CoreUnavailable("the core has not been reached yet, so its announcements are not known")
        return list(self._announcements)

    def next_announcement_run(self, announcement_id: str) -> Optional[datetime]:
        return self._next_runs.get(announcement_id)

    def announcement_clip(self, announcement: Announcement) -> str:
        """The clip name for an announcement: its recording, or its message as speech."""
        if announcement.asset_id:
            return ASSET_PREFIX + announcement.asset_id
        return TTS_PREFIX + announcement.message

    @property
    def config_version(self) -> Optional[int]:
        return self._config_version

    @property
    def controller(self) -> "RemoteCore":
        """`WeatherSupport` reads `host.controller.config`; this is the API's view of it."""
        return self

    # -- weather (the API's own state) -------------------------------------------------------------

    @property
    def weather_tracker(self):
        return self.weather.tracker

    @property
    def weather_alerts(self):
        return self.weather.alerts

    @property
    def weather_last_checked(self):
        return self.weather.last_checked

    @property
    def weather_last_error(self):
        return self.weather.last_error

    def weather_polled(self, geojson: dict, announce: bool = True) -> list[str]:
        return self.weather.polled(geojson, announce=announce)

    def weather_poll_failed(self, error: str) -> None:
        self.weather.poll_failed(error)

    def weather_alert_clip(self, alert) -> str:
        return self.weather.alert_clip(alert)

    def weather_schedule_due(self) -> bool:
        if self._config is None:  # the core has not been reached yet: nothing is due
            return False
        return self.weather.take_due_schedule()

    def scheduled_weather_text(self) -> Optional[str]:
        return self.weather.scheduled_text()

    def next_weather_schedule_run(self):
        return self.weather.next_schedule_run()

    def weather_summary_text(self) -> str:
        return self.weather.summary_text()

    # -- small things the API asks of a service ---------------------------------------------------------

    @property
    def net_active(self) -> bool:
        """Whether a net is on, as the core last said (False while nothing is known)."""
        return bool(self._snapshot and self._snapshot.net_active)

    def defer_announcement(self, clip: str, *, on_linked_radio: bool = False) -> None:
        """Hold an announcement until the net ends, then play it (the weather schedule does this when the
        repeater says nets should not be interrupted). With no net on it plays now. The oldest are dropped
        past MAX_DEFERRED. Held here, in the API: lost if the API restarts, as the in-process list is."""
        if not self.net_active:
            self.speak(clip, on_linked_radio=on_linked_radio)
            return
        self._deferred.append((clip, on_linked_radio))

    def _release_deferred(self) -> None:
        while self._deferred:
            clip, on_linked_radio = self._deferred.popleft()
            self.speak(clip, on_linked_radio=on_linked_radio)

    def held_reason(self, feature: str) -> str:
        """Why `feature` is off right now although it is switched on, else ''."""
        return _held_reason(self.saved_config, bool(self._snapshot and self._snapshot.net_active), feature)

    def add_config_listener(self, listener: Callable[[RepeaterConfig], None]) -> None:
        """Called with the settings in force whenever the core's configuration is read again."""
        self._listeners.append(listener)

    def idle_seconds(self) -> float:
        """How long the repeater has been quiet, as far as this client has seen (0 while it is in use)."""
        if self._snapshot is not None and (
            self._snapshot.state != "idle" or self._snapshot.cos_active or self._snapshot.ptt_active
        ):
            return 0.0
        since = self._busy_at if self._busy_at is not None else self._started_at
        return max(0.0, self._clock() - since)

    def sync_net_state(self, active: bool) -> None:
        """Remember what the API's saved state says about net mode, and tell the core: now if it is
        connected, and again after every reconnect (a restarted core forgets it)."""
        self._net_hint = active
        if self._connected:
            self._send_net_hint()

    def _send_net_hint(self) -> None:
        if self._net_hint is None:
            return
        try:
            task = asyncio.get_running_loop().create_task(self._send_net_hint_now(self._net_hint))
        except RuntimeError:
            return
        self._speaking.add(task)
        task.add_done_callback(self._speaking.discard)

    async def _send_net_hint_now(self, active: bool) -> None:
        try:
            await self.command("set_net_active", active=active)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001
            _logger.warning("could not tell the core about net mode: %s", type(error).__name__)

    # -- the AllStar link service's view of the links, on its way to the core --------------------------

    def link_event(self, event: ControllerEvent) -> None:
        """One thing the link service saw. Never waits and never raises: the events go to the core in order
        from a worker, and a core that is not there is brought up to date by `link_sync` when it is."""
        if self._link_state is None:
            self._link_state = {}
        state = self._link_state
        if isinstance(event, LinkStateChanged):
            if event.linked:
                state.setdefault(event.node_id, False)
            else:
                state.pop(event.node_id, None)
            sent = {"node_id": event.node_id, "event": "connected" if event.linked else "disconnected"}
        elif isinstance(event, RemoteKeyed):
            if event.keyed or event.node_id in state:
                state[event.node_id] = event.keyed
            sent = {"node_id": event.node_id, "event": "keyed" if event.keyed else "unkeyed"}
        else:
            return
        if not self._link_in_sync:
            self._queue_link("link_sync", {"nodes": dict(state)})  # the core is behind: bring it all up, not one event
        else:
            self._queue_link("link_event", sent)

    def link_sync(self, nodes: dict[str, bool]) -> None:
        """The whole current picture. Sent only if it differs from what the core already has, so a link
        service that cannot reach app_rpt and says so every few seconds does not repeat itself."""
        picture = dict(nodes)
        if self._link_in_sync and self._link_state == picture:
            return
        self._link_state = picture
        self._queue_link("link_sync", {"nodes": dict(picture)})

    def _queue_link(self, name: str, params: dict) -> None:
        if not self._connected:
            self._link_in_sync = False  # the picture is remembered, and sent when the core is back
            return
        if name == "link_sync":
            self._link_outbox.clear()  # a whole picture replaces anything older
        elif len(self._link_outbox) >= MAX_LINK_OUTBOX:
            self._link_outbox.clear()
            name, params = "link_sync", {"nodes": dict(self._link_state or {})}
            self._link_in_sync = False
        self._link_outbox.append((name, params))
        if name == "link_sync":
            self._link_in_sync = True  # the outbox is in order: whatever is queued after this follows it
        try:
            if self._link_wake is None:
                self._link_wake = asyncio.Event()
            if self._link_worker is None or self._link_worker.done():
                self._link_worker = asyncio.get_running_loop().create_task(self._send_links())
            self._link_wake.set()
        except RuntimeError:
            return  # no event loop: nothing to send with

    async def _send_links(self) -> None:
        assert self._link_wake is not None
        while True:
            await self._link_wake.wait()
            self._link_wake.clear()
            while self._link_outbox:
                name, params = self._link_outbox.popleft()
                try:
                    await self.command(name, **params)
                except asyncio.CancelledError:
                    raise
                except Exception as error:  # noqa: BLE001 - the link service must never be stopped by the core
                    self._link_in_sync = False  # the next event sends the whole picture instead
                    _logger.warning("could not tell the core about the links: %s", type(error).__name__)

    def _send_link_state(self) -> None:
        if self._link_state is not None:
            self._link_in_sync = False
            self._queue_link("link_sync", {"nodes": dict(self._link_state)})

    def wall_now(self) -> datetime:
        return datetime.now(timezone.utc)

    def snapshot(self) -> StatusSnapshot:
        """The last status the core reported, or a placeholder if there is none.

        The core pushes a status when something an operator would see changes, not
        every tick, so the countdowns (time-out, hang time, next ID) are carried
        forward by the time since the snapshot arrived. They only count down while
        the core is being heard from. Once `stale` the snapshot claims nothing about what the
        radio is doing: its state is `core_unavailable`, nothing is keyed or receiving, and
        no countdown runs. The things that do not change by the second (callsign, whether
        the transmitter is enabled, GMRS mode) are kept from the last status, and `status`
        still holds the last message the core sent, with its age in `age_s`."""
        if self._snapshot is None:
            return self._placeholder()
        if self.stale:
            return dataclasses.replace(
                self._snapshot, state=UNAVAILABLE_STATE, ptt_active=False, cos_active=False, ctcss_hz=None,
                linked_nodes=[], tot_remaining=None, hang_remaining=None, id_due_in=None,
            )
        elapsed = max(0.0, self._clock() - self._snapshot_at)

        def counted(value: Optional[float]) -> Optional[float]:
            return None if value is None else max(0.0, value - elapsed)

        snapshot = self._snapshot
        return dataclasses.replace(
            snapshot,
            tot_remaining=counted(snapshot.tot_remaining),
            hang_remaining=counted(snapshot.hang_remaining),
            id_due_in=counted(snapshot.id_due_in),
        )

    def subscribe(self) -> "asyncio.Queue[StatusSnapshot]":
        """A queue holding only the latest snapshot, like the in-process service's."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=1)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: "asyncio.Queue[StatusSnapshot]") -> None:
        self._subscribers.discard(queue)

    # -- following the stream ---------------------------------------------------------

    def _placeholder(self) -> StatusSnapshot:
        return StatusSnapshot(
            state=UNAVAILABLE_STATE, ptt_active=False, cos_active=False, ctcss_hz=None, linked_nodes=[],
            last_clip=None, timestamp=time.time(),
        )

    def _publish(self, snapshot: StatusSnapshot) -> None:
        for queue in self._subscribers:
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(snapshot)

    def _set_connected(self, connected: bool) -> None:
        if connected != self._connected:
            self._connected = connected
            _logger.info("core %s", "connected" if connected else "not reachable")
            if connected:
                self._send_net_hint()
                self._send_link_state()
            if not connected:
                # Tell subscribers the last value is no longer being confirmed.
                self._publish(self._placeholder())

    async def _run(self) -> None:
        attempt = 0
        while not self._stopping:
            try:
                await self._follow()
                attempt = 0  # it connected and then ended cleanly: start the backoff over
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001 - a bad stream must never end the loop
                if self._connected:
                    _logger.warning("core stream ended: %s", type(error).__name__)
                elif attempt == 0:
                    _logger.info("core not reachable yet (%s); retrying", type(error).__name__)
            self._set_connected(False)
            self.reconnects += 1
            await asyncio.sleep(RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)])
            attempt += 1

    async def _follow(self) -> None:
        timeout = httpx.Timeout(CONNECT_TIMEOUT_SECONDS, read=None)
        async with self._socket_client(timeout) as client:
            handles = ",".join(sorted(self._macro_handlers))
            async with client.stream("GET", "/events" + (f"?handles={handles}" if handles else "")) as response:
                if response.status_code != 200:
                    raise RuntimeError(f"core answered {response.status_code}")
                lines = response.aiter_lines()
                event, data = "message", []
                while True:
                    # Any line, a heartbeat included, proves the core is alive.
                    try:
                        line = await asyncio.wait_for(lines.__anext__(), STALE_AFTER_SECONDS)
                    except StopAsyncIteration:
                        return
                    if len(line) > MAX_LINE_BYTES:
                        raise RuntimeError("event line too long")
                    if line.startswith(":"):
                        continue
                    if line:
                        field, _, value = line.partition(":")
                        value = value[1:] if value.startswith(" ") else value
                        if field == "event":
                            event = value
                        elif field == "data":
                            data.append(value)
                        continue
                    if data:  # a blank line ends one event
                        self._handle(event, "\n".join(data))
                    event, data = "message", []

    def _handle(self, event: str, raw: str) -> None:
        try:
            payload = json.loads(raw)
            if event == "status":
                status = CoreStatus.model_validate(payload["status"])
                local = payload.get("local") or {}
                self._status = status
                was_locked = bool(self._snapshot and self._snapshot.locked_out)
                self._snapshot = _snapshot_from(status, local)
                self._snapshot_at = self._clock()
                if status.state != "idle" or status.cos_active or status.ptt_commanded:
                    self._busy_at = self._snapshot_at
                if self.lockout_hook is not None and was_locked != self._snapshot.locked_out and self._seen_before:
                    self.lockout_hook(self._snapshot.locked_out)
                self._seen_before = True
                self._seen()
                self._publish(self._snapshot)
                if self._deferred and not self.net_active:
                    self._release_deferred()  # the net ended: play what was held for it
                version = local.get("config_version")
                if isinstance(version, int) and version != self._config_version:
                    self._announced_version = version
                    self._refresh_config()
            elif event == "macro":
                self._seen()
                self._run_macro_handler(payload)
            elif event == "heartbeat":
                self._health = str(payload.get("health"))
                self._seen()
        except Exception as error:  # noqa: BLE001 - one malformed event is skipped, not fatal
            _logger.warning("ignored a bad %s event from the core: %s", event, type(error).__name__)

    def _run_macro_handler(self, payload: dict) -> None:
        """A macro action for one of our modules. Never raises, and never logs the arguments
        (a mailbox PIN, a Home Assistant target)."""
        family = payload.get("family")
        handler = self._macro_handlers.get(family)
        if handler is None:
            return
        try:
            args = list(payload.get("args") or [])
            if family == "mailbox" and args:
                args = [MailboxCommand(**args[0])]
            result = handler(*args)
            if family in ("aprs", "weather") and isinstance(result, str):
                self.speak(TTS_PREFIX + result)
            elif asyncio.iscoroutine(result):
                task = asyncio.ensure_future(result)
                self._speaking.add(task)
                task.add_done_callback(self._speaking.discard)
        except Exception as error:  # noqa: BLE001 - one bad action must not end the stream
            _logger.warning("a %s macro action failed: %s", family, type(error).__name__)

    # -- the configuration ---------------------------------------------------------------------

    def _client(self) -> httpx.AsyncClient:
        return self._socket_client(httpx.Timeout(REQUEST_TIMEOUT_SECONDS, connect=CONNECT_TIMEOUT_SECONDS))

    def _socket_client(self, timeout: httpx.Timeout) -> httpx.AsyncClient:
        """A client for the core's socket. It is plain HTTP over a Unix socket, so `verify=False`: httpx
        otherwise builds a TLS context for every client, loading and parsing the whole certificate
        bundle, which on a Pi was a sixth of the API's Python time (a client is made per request)."""
        transport = httpx.AsyncHTTPTransport(uds=str(self.socket_path), verify=False)
        return httpx.AsyncClient(transport=transport, base_url="http://core", timeout=timeout)

    def _refresh_config(self) -> None:
        """Read the configuration (and programming) again, soon. One read runs at a time. A
        request that arrives while one runs is not dropped: the running read goes round again."""
        self._read_again = True
        if self._fetching is None or self._fetching.done():
            self._fetching = asyncio.ensure_future(self._fetch_config_quietly())

    async def _fetch_config_quietly(self) -> None:
        try:
            while self._read_again:
                self._read_again = False
                await self._fetch_config()
                await self._fetch_programming()
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - the next status event tries again
            self._read_again = False
            _logger.warning("could not read the core's configuration: %s", type(error).__name__)

    async def _fetch_config(self) -> None:
        # Read again while the core has announced a newer version than the one we got. A
        # new announcement that arrives mid-read cannot start its own read (one at a time),
        # so this loop is what keeps it from being lost. Bounded: a stale announcement, for
        # example from before a core restart, must not make us spin.
        for _ in range(MAX_CONFIG_READS):
            async with self._client() as client:
                response = await client.get("/config")
            if response.status_code != 200 or len(response.content) > MAX_CONFIG_BYTES:
                raise RuntimeError(f"core answered {response.status_code}")
            state = ConfigState.model_validate_json(response.content)
            self._config = RepeaterConfig.from_flat_dict(state.values)  # values are never logged
            self._config_version = state.config_version
            for listener in list(self._listeners):
                try:
                    listener(self.config)
                except Exception as error:  # noqa: BLE001 - one listener must not stop the others
                    _logger.warning("a configuration listener failed: %s", type(error).__name__)
            if self._announced_version is None or self._announced_version == state.config_version:
                return

    def hardware_status(self, name: str) -> dict:
        """What the core last reported for one of its hardware parts (`gpio`, `link_radio`, ...).
        Raises `CoreUnavailable` if the core has not been heard from lately, or did not report that
        part, so a page never shows an old or invented state."""
        if (
            self._hardware_at is None
            or self.stale
            or self._clock() - self._hardware_at > HARDWARE_STALE_SECONDS
        ):
            raise CoreUnavailable("the core is not being heard from, so its hardware state is not known")
        try:
            return self._hardware[name]
        except KeyError:
            raise CoreUnavailable(f"the core did not report its {name} state") from None

    async def _pull_hardware(self) -> None:
        while True:
            try:
                if self._connected:
                    async with self._client() as client:
                        response = await client.get("/hardware")
                    if response.status_code == 200 and len(response.content) <= MAX_CONFIG_BYTES:
                        hardware = CoreHardware.model_validate_json(response.content)
                        self._hardware, self._hardware_at = dict(hardware.statuses), self._clock()
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001 - the pages say "not known" until the next read
                _logger.debug("could not read the core's hardware state: %s", type(error).__name__)
            await asyncio.sleep(HARDWARE_POLL_SECONDS)

    async def _arm_capture(self, max_seconds: float) -> None:
        try:
            await self.command("arm_capture", max_seconds=max_seconds)
        except (CommandRefused, CoreUnavailable):
            self._capture_waiting = None  # nothing will come
            self.speak(TTS_PREFIX + "Messages need live audio, which is not running.")

    async def _captured(self, filename: str, started_at: float) -> None:
        """The core recorded a transmission for us and put it in a file. Hand it to whoever armed it
        (the mailbox), on a worker thread as in process, and remove the file. Only the core's own
        recording files are ever opened: a path that is not one is ignored."""
        path = Path(filename)
        if path.parent.name != "captures" or not _CAPTURE_NAME.fullmatch(path.name):
            _logger.warning("ignored a recording that is not one of the core's files")
            return
        waiting, self._capture_waiting = self._capture_waiting, None
        loop = asyncio.get_running_loop()
        try:
            if waiting is None:
                _logger.warning("a recorded transmission arrived and no one was waiting for it; it was dropped")
                return
            try:
                samples, _rate = await loop.run_in_executor(None, lambda: decode_wav(path.read_bytes()))
            except (OSError, WavError):
                _logger.warning("could not read the recorded transmission")
                return
            await loop.run_in_executor(None, waiting, samples, float(started_at))
        finally:
            path.unlink(missing_ok=True)

    def remote_audio_monitor(self, device: str) -> RemoteAudioMonitor:
        """The core's live audio (`live` or `monitor`), read over the socket for as long as someone listens."""
        if device not in self._audio_monitors:
            self._audio_monitors[device] = RemoteAudioMonitor(self.socket_path, device)
        return self._audio_monitors[device]

    def remote_activity_store(self) -> RemoteActivityStore:
        """The activity history, which the core records and keeps; this reads it over the socket."""
        return RemoteActivityStore(self.socket_path)

    def attach_log_hub(self, hub) -> None:
        """Publish the core's log lines into `hub`, so the dashboard's Logs page, the live stream, the
        support bundle and remote shipping show the core and the API together. Call before `start()`."""
        if self._task is not None:
            raise RuntimeError("attach the log hub before start()")
        self._log_hub = hub

    async def _pull_logs(self) -> None:
        """Read the core's log a little at a time. The core keeps a ring of recent lines; this keeps
        up with it and, after the core restarts (its sequence starts over), begins again from the
        start of the new run. Lines logged while the API was away are read when it is back, as far as
        the core's ring reaches."""
        while True:
            more = False
            try:
                if self._connected:
                    more = await self._pull_logs_once()
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001 - the Logs page is not worth a crash
                _logger.warning("could not read the core's log: %s", type(error).__name__)
            await asyncio.sleep(0 if more else LOG_POLL_SECONDS)

    async def _pull_logs_once(self) -> bool:
        """One read. True if the core had more than it returned (read again at once)."""
        async with self._client() as client:
            response = await client.get(f"/logs?after={self._log_cursor}")
        if response.status_code != 200 or len(response.content) > MAX_CONFIG_BYTES:
            return False
        logs = CoreLogs.model_validate_json(response.content)
        if self._log_boot != logs.header.boot_id:
            restarted = self._log_boot is not None
            self._log_boot = logs.header.boot_id
            if restarted:  # a new run: its sequence starts over, so read it from the start
                self._log_cursor = 0
                return True
        for entry in logs.lines:
            self._log_hub.publish(str(entry.get("line", "")), str(entry.get("level", "INFO")), at=entry.get("at"))
        if logs.lines:
            self._log_cursor = int(logs.lines[-1]["seq"])
        return bool(logs.lines) and self._log_cursor < logs.cursor

    async def _fetch_programming(self) -> None:
        async with self._client() as client:
            response = await client.get("/programming")
        if response.status_code != 200 or len(response.content) > MAX_CONFIG_BYTES:
            raise RuntimeError(f"core answered {response.status_code}")
        state = ProgrammingState.model_validate_json(response.content)
        macro_fields = {f.name for f in dataclasses.fields(Macro)}
        announcement_fields = {f.name for f in dataclasses.fields(Announcement)}
        macros = [Macro(**{k: v for k, v in item.items() if k in macro_fields}) for item in state.macros]
        announcements, runs = [], {}
        for item in state.announcements:
            announcement = Announcement(**{k: v for k, v in item.items() if k in announcement_fields})
            announcements.append(announcement)
            when = item.get("next_run")
            runs[announcement.id] = datetime.fromisoformat(when) if when else None
        self._macros, self._announcements, self._next_runs = macros, announcements, runs

    async def render_and_queue(self, clip: str, *, on_linked_radio: bool = False) -> None:
        """Have the core render a clip and queue it to play, like `CoreRuntime.render_and_queue`,
        and raise the same errors: `UnknownClipError`, `TTSError`, `AnnouncementQueueFull`.
        Raises `CoreUnavailable` if the core could not be reached."""
        try:
            await self.command("queue_clip", clip=clip, on_linked_radio=on_linked_radio)
        except CommandRefused as refused:
            if refused.code == "command.unknown_clip":
                raise UnknownClipError(refused.message) from None
            if refused.code in ("command.render_failed", "command.render_timeout"):
                raise TTSError(refused.message) from None
            if refused.code == "command.queue_full":
                raise AnnouncementQueueFull(refused.message) from None
            raise

    def speak(self, clip: str, *, on_linked_radio: bool = False) -> None:
        """Queue a clip without waiting, as the service's `speak` does in process: the
        caller is not told how it went, and a failure is logged (never the clip, which can
        be the text of an announcement). Needs a running event loop."""
        try:
            task = asyncio.get_running_loop().create_task(self._speak_quietly(clip, on_linked_radio))
        except RuntimeError:
            _logger.warning("speak() needs a running event loop; the clip was dropped")
            return
        self._speaking.add(task)
        task.add_done_callback(self._speaking.discard)

    async def _speak_quietly(self, clip: str, on_linked_radio: bool) -> None:
        try:
            await self.render_and_queue(clip, on_linked_radio=on_linked_radio)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - nobody is waiting on this
            _logger.warning("could not queue a spoken clip: %s", type(error).__name__)

    async def recent_link_events(self, limit: int = 50) -> list[dict]:
        """Newest-first link activity, read from the core when asked (it is not cached)."""
        try:
            async with self._client() as client:
                response = await client.get("/link-events")
        except httpx.HTTPError:
            raise CoreUnavailable("the core is not reachable, so link activity cannot be read") from None
        if response.status_code != 200 or len(response.content) > MAX_CONFIG_BYTES:
            raise CoreUnavailable("the core did not answer the request for link activity")
        try:
            events = LinkEvents.model_validate_json(response.content).events
        except ValueError:
            raise CoreUnavailable("the core's answer about link activity was not understood") from None
        return events[: max(0, limit)]

    def _header(self) -> Header:
        return Header(
            schema_version=1, message_id=new_id(), emitted_at=datetime.now(timezone.utc),
            boot_id=self._sequencer.boot_id, seq=self._sequencer.next(),
            source=Source(module="api", version="0.1.0"),
        )

    async def _exchange(self, path: str, body: str) -> httpx.Response:
        """POST to the core. Raises `CoreUnavailable` for anything that is not an answer,
        and says whether the request may have been applied."""
        try:
            async with self._client() as client:
                return await client.post(path, content=body, headers={"Content-Type": "application/json"})
        except httpx.ConnectError:
            raise CoreUnavailable("the core is not reachable; nothing was changed") from None
        except httpx.TimeoutException:
            raise CoreUnavailable("the core did not answer in time; it may or may not have been applied") from None
        except httpx.HTTPError:
            raise CoreUnavailable("the connection to the core failed; it may or may not have been applied") from None

    async def command(self, name: str, **params: object) -> None:
        """Send the core a one-shot command and wait for its answer. Raises `CommandRefused`
        if the core refused it (nothing was done) and `CoreUnavailable` if it could not be
        reached or did not answer cleanly."""
        try:
            request = CoreCommand(header=self._header(), command=name, params=dict(params))
        except ValueError:
            raise CommandRefused("command.invalid", f"not a valid {name} command") from None
        response = await self._exchange("/command", request.model_dump_json())
        if response.status_code != 200:
            raise CommandRefused("command.invalid", "the core did not accept the request")
        if name == "set_net_active" and isinstance(params.get("active"), bool):
            self._net_hint = params["active"]
        try:
            result = CommandResult.model_validate_json(response.content)
        except ValueError:
            raise CoreUnavailable("the core's answer was not understood; it may or may not have been applied") from None
        if result.header.request_id != request.header.message_id:
            raise CoreUnavailable("the core's answer did not match the request; it may or may not have been applied")
        if result.status is CommandStatus.REJECTED:
            error = result.error
            raise CommandRefused(
                error.code if error else "command.rejected", error.message if error else "the core refused the command",
                retryable=bool(error and error.retryable),
            )
        if request.command in PROGRAMMING or request.command is CommandName.IMPORT_SNAPSHOT:
            try:
                if request.command is CommandName.IMPORT_SNAPSHOT:
                    await self._fetch_config()
                await self._fetch_programming()  # so the lists a route returns next are current
            except Exception:  # noqa: BLE001 - it IS applied; the cache catches up on the next event
                _logger.warning("the change was applied but the lists could not be read back")
                self._refresh_config()

    async def change_config(self, **overrides: object) -> RepeaterConfig:
        """Ask the core to change settings and wait for its answer.

        Returns the saved configuration the core confirms. Raises `ConfigRefused` if the
        core checked the change and refused it (nothing was applied) and `CoreUnavailable`
        if the core could not be reached or did not answer cleanly; in that case the
        change may or may not have been applied, and the message says so."""
        request = ConfigChange(
            header=self._header(), values=dict(overrides),
        )
        response = await self._exchange("/config", request.model_dump_json())
        if response.status_code != 200:
            raise ConfigRefused("config.invalid", "the core did not accept the request")
        try:
            applied = ConfigApplied.model_validate_json(response.content)
        except ValueError:
            raise CoreUnavailable("the core's answer was not understood; the change may or may not have been applied") from None
        if applied.header.request_id != request.header.message_id:
            raise CoreUnavailable("the core's answer did not match the request; the change may or may not have been applied")
        if applied.result is ConfigResult.REJECTED:
            error = applied.error
            raise ConfigRefused(
                error.code if error else "config.rejected", error.message if error else "the core refused the change",
                retryable=bool(error and error.retryable),
            )
        self._announced_version = applied.config_version
        try:
            await self._fetch_config()  # so what we return is what the core now has
        except Exception:  # noqa: BLE001 - the change IS applied; the cache catches up on the next event
            _logger.warning("the change was applied but the configuration could not be read back")
            self._refresh_config()
            if self._config is None:
                raise CoreUnavailable("applied, but the core's configuration could not be read back") from None
        return self.saved_config

    def _seen(self) -> None:
        self._last_event_at = self._clock()
        self._set_connected(True)


def _snapshot_from(status: CoreStatus, local: dict) -> StatusSnapshot:
    """Rebuild the dashboard's snapshot from the core's contract message."""
    return StatusSnapshot(
        state=status.state,
        # The controller's intent, as the in-process status always reported it.
        ptt_active=status.ptt_commanded,
        cos_active=status.cos_active,
        ctcss_hz=status.ctcss_hz,
        linked_nodes=list(status.linked_nodes),
        last_clip=local.get("last_clip"),
        timestamp=float(local.get("timestamp") or time.time()),
        transmitter_enabled=status.transmitter_enabled,
        locked_out=status.locked_out,
        net_active=status.net_active,
        gmrs_mode=status.gmrs_mode,
        callsign=status.callsign,
        tot_remaining=status.tot_remaining_s,
        hang_remaining=status.hang_remaining_s,
        id_due_in=status.id_due_in_s,
    )


#: The files the core records into: `capture-` and sixteen hex digits.
_CAPTURE_NAME = re.compile(r"capture-[0-9a-f]{16}\.wav")


class _RemoteCapture:
    """What `service.audio_output` is when the core is a separate process: the one thing the mailbox
    asks of it, recording the next transmission instead of repeating it."""

    def __init__(self, core: RemoteCore) -> None:
        self._core = core

    def arm_capture(self, max_seconds: float, on_done: Callable) -> bool:
        """Ask the core to record the next transmission and call `on_done(samples, started_at)` with it.
        False, at once, if the core is not running live audio or cannot be heard from. A core that
        refuses after this said yes is spoken about by `RemoteCore._arm_capture`."""
        core = self._core
        try:
            if not core.hardware_status("live_audio").get("running"):
                return False
        except CoreUnavailable:
            return False
        core._capture_waiting = on_done  # one slot, as in the engine: a newer arm replaces an older one
        task = asyncio.ensure_future(core._arm_capture(max_seconds))
        core._speaking.add(task)
        task.add_done_callback(core._speaking.discard)
        return True
