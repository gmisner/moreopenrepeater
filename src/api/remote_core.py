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
import dataclasses
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx

from contracts import (
    CommandResult,
    CommandStatus,
    ConfigApplied,
    ConfigChange,
    ConfigResult,
    ConfigState,
    CoreCommand,
    CoreStatus,
    Header,
    Sequencer,
    Source,
    new_id,
)
from controller.config import RepeaterConfig
from controller.modes import effective_config

from .service.core_handle import CommandRefused, ConfigRefused, CoreUnavailable
from .service.snapshot import StatusSnapshot

_logger = logging.getLogger("moreopenrepeater.remote_core")

#: No event of any kind (status or heartbeat) for this long means the stream is dead.
#: The core sends a heartbeat every 5 s.
STALE_AFTER_SECONDS = 15.0
CONNECT_TIMEOUT_SECONDS = 2.0
RETRY_DELAYS = (0.5, 1.0, 2.0, 5.0)  # then the last one, forever
MAX_LINE_BYTES = 64 * 1024
REQUEST_TIMEOUT_SECONDS = 5.0  # a write must not hang the dashboard
MAX_CONFIG_BYTES = 1024 * 1024  # what we will read back from the core

#: What a snapshot says while nothing is known. The state is not one the controller
#: has, so a dashboard cannot mistake it for the radio's state.
UNAVAILABLE_STATE = "core_unavailable"


class RemoteCore:
    def __init__(self, socket_path: Path, *, clock=time.monotonic) -> None:
        self.socket_path = socket_path
        self._clock = clock
        self._snapshot: Optional[StatusSnapshot] = None
        self._snapshot_at: float = 0.0  # when `_snapshot` arrived, on `clock`
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
        self._sequencer = Sequencer()

    # -- lifecycle ------------------------------------------------------------

    def start(self) -> None:
        """Start following the core. Never raises, and does not wait: the core may not be up yet."""
        if self._task is None:
            self._stopping = False
            self._task = asyncio.ensure_future(self._run())

    async def stop(self) -> None:
        # The flag matters: on Python 3.11 a cancel that lands inside the HTTP client while
        # we are retrying a dead socket can be swallowed, and then only the flag ends the loop.
        self._stopping = True
        for name in ("_task", "_fetching"):
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

    @property
    def config_version(self) -> Optional[int]:
        return self._config_version

    def wall_now(self) -> datetime:
        return datetime.now(timezone.utc)

    def snapshot(self) -> StatusSnapshot:
        """The last status the core reported, or a placeholder if there is none.

        The core pushes a status when something an operator would see changes, not
        every tick, so the countdowns (time-out, hang time, next ID) are carried
        forward by the time since the snapshot arrived. They only count down while
        the core is being heard from; once `stale` they are frozen at their last
        value. While `stale` is true the whole snapshot is the last known value and
        may be old."""
        if self._snapshot is None:
            return self._placeholder()
        if self.stale:
            return self._snapshot
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
                if self._connected or attempt == 0:
                    _logger.warning("core stream ended: %s", error)
            self._set_connected(False)
            self.reconnects += 1
            await asyncio.sleep(RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)])
            attempt += 1

    async def _follow(self) -> None:
        transport = httpx.AsyncHTTPTransport(uds=str(self.socket_path))
        timeout = httpx.Timeout(CONNECT_TIMEOUT_SECONDS, read=None)
        async with httpx.AsyncClient(transport=transport, base_url="http://core", timeout=timeout) as client:
            async with client.stream("GET", "/events") as response:
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
                self._snapshot = _snapshot_from(status, local)
                self._snapshot_at = self._clock()
                self._seen()
                self._publish(self._snapshot)
                version = local.get("config_version")
                if isinstance(version, int) and version != self._config_version:
                    self._announced_version = version
                    self._refresh_config()
            elif event == "heartbeat":
                self._health = str(payload.get("health"))
                self._seen()
        except Exception as error:  # noqa: BLE001 - one malformed event is skipped, not fatal
            _logger.warning("ignored a bad %s event from the core: %s", event, type(error).__name__)

    # -- the configuration ---------------------------------------------------------------------

    def _client(self) -> httpx.AsyncClient:
        transport = httpx.AsyncHTTPTransport(uds=str(self.socket_path))
        timeout = httpx.Timeout(REQUEST_TIMEOUT_SECONDS, connect=CONNECT_TIMEOUT_SECONDS)
        return httpx.AsyncClient(transport=transport, base_url="http://core", timeout=timeout)

    def _refresh_config(self) -> None:
        """Read the configuration again, soon. One read at a time; it picks up the newest."""
        if self._fetching is None or self._fetching.done():
            self._fetching = asyncio.ensure_future(self._fetch_config_quietly())

    async def _fetch_config_quietly(self) -> None:
        try:
            await self._fetch_config()
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - the next status event tries again
            _logger.warning("could not read the core's configuration: %s", type(error).__name__)

    async def _fetch_config(self) -> None:
        async with self._client() as client:
            response = await client.get("/config")
        if response.status_code != 200 or len(response.content) > MAX_CONFIG_BYTES:
            raise RuntimeError(f"core answered {response.status_code}")
        state = ConfigState.model_validate_json(response.content)
        self._config = RepeaterConfig.from_flat_dict(state.values)  # values are never logged
        self._config_version = state.config_version
        if self._announced_version is not None and self._announced_version != state.config_version:
            self._refresh_config()  # it moved on while we were reading

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
