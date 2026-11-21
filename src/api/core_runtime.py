"""The autonomous core's start-up and shutdown, apart from the web app.

Stage 3 slice 5 of the modular migration (#229). `CoreRuntime` owns the steps
that keep the repeater repeating: the controller tick, announcements and the
long ID, text-to-speech, the audio engines, the link radio, AllStar, DMR and
autopatch audio, the GPIO and TX fan, and the systemd watchdog.

It imports no web framework. `AppRuntime` still runs everything in one
process and calls these steps in exactly the order the lifespan always did, so
nothing changes for the running service. A core that runs alone (a later
slice) calls the same steps without the API's.

Order matters here: audio and PTT are not live until text-to-speech is ready
(`start_tts` waits for it), and `READY=1` is only sent once the audio engines
and links have started.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Callable, Optional

from playout.kokoro_service import KokoroService, KokoroServiceError
from playout.piper_service import PiperService, PiperServiceError, wait_until_ready
from playout.renderer import ClipRenderer, UnknownClipError
from playout.tts import TTSError

from observability import log_event

from .allstar_audio import UsrpSettings
from .audio_devices import AudioDeviceList
from .boards import run_amixer
from .dmr_audio import DmrUsrpSettings
from .host_hardware import SerialPortList
from .loops import run_announcement_loop, run_long_id_warm_loop, run_tick_loop
from .usrp_endpoints import Peer, UsrpEndpoints
from .watchdog import Watchdog, ping_interval, sd_notify

TICK_INTERVAL_SECONDS = 0.05
ANNOUNCEMENT_POLL_SECONDS = 1.0
LONG_ID_WARM_SECONDS = 20.0
INTEGRATION_STOP_SECONDS = 2.0  # at shutdown, how long each integration gets to stop before the next one is tried
PIPER_RETRY_MAX_SECONDS = 30.0  # while Piper's container is down, the core looks again this often at most

_tts_logger = logging.getLogger("moreopenrepeater.tts")
_announce_logger = logging.getLogger("moreopenrepeater.announcements")
_core_logger = logging.getLogger("moreopenrepeater.core")


class AnnouncementQueueFull(Exception):
    """Too many announcements are already waiting to play."""


class EndpointRefused(Exception):
    """The audio could not be pointed there; the message says why, for the dashboard to show."""


def kokoro_service_for(renderer: ClipRenderer) -> Optional[KokoroService]:
    """Build a serve manager when the clip renderer is using Kokoro."""
    tts = getattr(renderer, "tts", None)
    if tts is None or getattr(tts, "name", None) != "kokoro":
        return None
    binary = getattr(tts, "binary", "") or "kokoro"
    return KokoroService(binary=binary)


def piper_service_for(renderer: ClipRenderer, voice: str = "") -> Optional[PiperService]:
    """Build an HTTP serve manager when the clip renderer is using Piper."""
    tts = getattr(renderer, "tts", None)
    if tts is None or getattr(tts, "name", None) != "piper":
        return None
    from playout.tts import piper_service_external, piper_service_url, sanitize_piper_voice

    if not piper_service_url() or piper_service_external():  # an external one is not ours to start or stop
        return None
    return PiperService(voice=sanitize_piper_voice(voice))


class CoreRuntime:
    def __init__(
        self,
        *,
        service,
        renderer: ClipRenderer,
        live_audio,
        monitor_receiver,
        link_radio,
        remote_base,
        gpio,
        tx_fan,
        autopatch,
        allstar_node,
        dmr,
        dmr_audio,
        watchdog: Watchdog,
        remember_stall: Callable[[str], None] = lambda stalled: None,
        usrp_endpoints: Optional[UsrpEndpoints] = None,
        run_mixer: Callable = run_amixer,
    ) -> None:
        self.service = service
        self.renderer = renderer
        self.live_audio = live_audio
        self.monitor_receiver = monitor_receiver
        self.link_radio = link_radio
        self.remote_base = remote_base
        self.gpio = gpio
        self.tx_fan = tx_fan
        self.autopatch = autopatch
        self.run_mixer = run_mixer  # runs amixer for the API (the `mixer_run` command): the core has the sound card
        self.audio_device_list = AudioDeviceList()  # the sound cards, reported to the API (it has no audio libraries)
        self.serial_port_list = SerialPortList()  # the serial ports the core was given, reported to the API (it has no devices)
        self.allstar_node = allstar_node
        self.dmr = dmr
        self.dmr_audio = dmr_audio
        self.watchdog = watchdog
        self.usrp_endpoints = usrp_endpoints
        self.kokoro_service: Optional[KokoroService] = None
        self.piper_service: Optional[PiperService] = None
        self._piper_watch: Optional[asyncio.Task] = None
        #: When the controller tick last ran; the watchdog watches it.
        self.last_tick: list[Optional[float]] = [None]

        watchdog.watch("controller", lambda: self.last_tick[0])
        watchdog.watch("audio engine", live_audio.progress)
        watchdog.watch("link radio audio engine", link_radio.progress)

        def before_watchdog_restart(stalled: str) -> None:
            try:
                try:
                    live_audio.release_ptt()
                finally:
                    link_radio.release_ptt()  # unkey the link radio even if the main one failed
            finally:
                # Remember even if unkey fails so the next start reports a watchdog
                # restart instead of a generic crash.
                remember_stall(stalled)

        watchdog.on_stall = before_watchdog_restart

    # -- announcements --------------------------------------------------------

    async def render_and_queue(self, clip: str, *, on_linked_radio: bool = False) -> None:
        """Render off the event loop first, so the controller knows how long
        the clip is (and a TTS failure surfaces here, not mid-transmission)."""
        await asyncio.get_running_loop().run_in_executor(None, self.renderer.render, clip, self.service.config)
        if not self.service.queue_announcement(clip, on_linked_radio=on_linked_radio):
            raise AnnouncementQueueFull("Too many announcements are already waiting to play")

    # -- start-up steps, called in this order by the runtime -----------------------

    def start_fan(self) -> None:
        # Before the audio engines, so a pin taken from the fan is released before they claim it.
        self.tx_fan.start()

    def start_tick(self, tasks: list) -> None:
        tasks.append(asyncio.create_task(run_tick_loop(self.service, self.last_tick, TICK_INTERVAL_SECONDS)))

    def start_announcements(self, tasks: list) -> None:
        tasks.append(
            asyncio.create_task(
                run_announcement_loop(
                    self.service,
                    self.render_and_queue,
                    ANNOUNCEMENT_POLL_SECONDS,
                    (UnknownClipError, TTSError, AnnouncementQueueFull),
                    _announce_logger,
                )
            )
        )

    async def start_tts(self) -> None:
        """Start the Kokoro or Piper server this process owns and wait for it, so audio and PTT are not live until it is
        ready (inventory finding F10). Piper in its own container is not waited for: it is followed in the background."""
        loop = asyncio.get_running_loop()
        kokoro_service = kokoro_service_for(self.renderer)
        if kokoro_service is not None:
            try:
                await loop.run_in_executor(None, kokoro_service.start_and_wait)
                self.kokoro_service = kokoro_service
            except KokoroServiceError:
                log_event(
                    _tts_logger, logging.ERROR, "tts.service_failed",
                    "Kokoro service failed to become ready; voice IDs fall back to CW until it works",
                    error_code="tts.service", engine="kokoro", exc_info=True, facts_in_text=False,
                )
                await loop.run_in_executor(None, kokoro_service.stop)
        self._watch_external_piper(loop)
        piper_service = piper_service_for(self.renderer, voice=self.service.config.tts_voice)
        if piper_service is not None:
            try:
                await loop.run_in_executor(None, piper_service.start_and_wait)
                self.piper_service = piper_service
            except PiperServiceError:
                log_event(
                    _tts_logger, logging.ERROR, "tts.service_failed",
                    "Piper service failed to become ready; voice IDs use CLI/espeak until it works",
                    error_code="tts.service", engine="piper", exc_info=True, facts_in_text=False,
                )
                await loop.run_in_executor(None, piper_service.stop)

    def _watch_external_piper(self, loop: asyncio.AbstractEventLoop) -> None:
        """Piper in its own container: never wait for it. Audio starts at once, and a task looks for Piper in the
        background (see `_follow_external_piper`). Until it answers, a clip that needs speech uses the fallback voice.
        Nothing is started or stopped here: Piper is not this process's."""
        from playout.tts import piper_service_external

        tts = getattr(self.renderer, "tts", None)
        if tts is None or getattr(tts, "name", None) != "piper" or not piper_service_external():
            return
        self._piper_watch = asyncio.create_task(self._follow_external_piper(loop))

    async def _follow_external_piper(self, loop: asyncio.AbstractEventLoop) -> None:
        """Look for the Piper container until it answers, then stop. If it was not up at the first look, clips that
        were spoken by the fallback voice in the meantime are cached, so drop them and render them again."""
        from playout.tts import piper_service_url

        delay, was_down = min(1.0, PIPER_RETRY_MAX_SECONDS), False
        while not await loop.run_in_executor(None, wait_until_ready, piper_service_url(), 0):
            if not was_down:
                was_down = True
                _tts_logger.warning("the Piper service is not answering; voice IDs use the fallback voice until it does")
            await asyncio.sleep(delay)
            delay = min(2 * delay, PIPER_RETRY_MAX_SECONDS)
        log_event(_tts_logger, logging.INFO, "tts.service_ready", "the Piper service is ready", engine="piper", facts_in_text=False)
        if was_down:
            self.renderer.clear_cache()
            await loop.run_in_executor(None, self.renderer.warm, self.service.config)

    def warm_renderer(self, loop: asyncio.AbstractEventLoop) -> None:
        loop.run_in_executor(None, self.renderer.warm, self.service.config)
        self.service.add_config_listener(lambda config: loop.run_in_executor(None, self.renderer.warm, config))

    def start_long_id(self, tasks: list) -> None:
        tasks.append(
            asyncio.create_task(
                run_long_id_warm_loop(
                    self.renderer.warm_long_id, lambda: self.service.config, LONG_ID_WARM_SECONDS, _announce_logger
                )
            )
        )

    async def start_audio_and_links(self, loop: asyncio.AbstractEventLoop, tasks: list) -> None:
        self.live_audio.attach(loop)
        self.monitor_receiver.attach(loop)
        self.link_radio.attach(loop)
        tasks.append(asyncio.create_task(self.link_radio.run()))
        await self.autopatch.start()
        await self.allstar_node.start()
        saved = self.usrp_endpoints.load() if self.usrp_endpoints else {}
        if not self.allstar_node.manual and "allstar" in saved and self.allstar_node.audio.settings is None:
            await self._point_allstar(saved["allstar"])  # where the dashboard last set the node up
        if self.dmr_audio.settings is None and "dmr" in saved:
            await self._point_dmr(saved["dmr"])
        elif self.dmr_audio.settings is not None and (
            self.dmr.directory is None or (self.dmr.directory / "enabled").exists()
        ):
            await self.dmr_audio.start()  # a bridge in its own container has no directory here: its settings are enough

    async def configure_usrp(self, service: str, peer: Optional[Peer]) -> None:
        """The dashboard set the AllStar node or the DMR bridge up (or turned it off, `peer` None): point the audio at it.
        Kept, so the next start of the core does the same without the API. Raises `EndpointRefused` with the reason."""
        if service == "allstar":
            if self.allstar_node.manual:
                raise EndpointRefused("The AllStar node is set by MOREOPENREPEATER_USRP_NODE in the core's settings; change it there.")
            await self._point_allstar(peer)
        else:
            await self._point_dmr(peer)
        if self.usrp_endpoints is not None:
            self.usrp_endpoints.save(service, peer)

    async def _point_allstar(self, peer: Optional[Peer]) -> None:
        audio = self.allstar_node.audio
        listen = self.allstar_node.listen
        await audio.configure(UsrpSettings(listen[0], listen[1], peer[0], peer[1]) if peer else None)
        if peer and audio.error:
            raise EndpointRefused(audio.error)

    async def _point_dmr(self, peer: Optional[Peer]) -> None:
        audio = self.dmr_audio
        await audio.configure(DmrUsrpSettings(audio.listen[0], audio.listen[1], peer[0], peer[1]) if peer else None)
        if peer and audio.error:
            raise EndpointRefused(audio.error)

    def notify_ready(self) -> None:
        sd_notify("READY=1")
        interval = ping_interval()
        if interval is None and self.watchdog.restart is not None:
            interval = self.watchdog.container_interval()  # no systemd to ping: check on our own timer
        if interval is not None:
            self.watchdog.start(interval)

    def start_io(self) -> None:
        self.gpio.start()
        self.remote_base.attach(asyncio.get_running_loop())

    # -- shutdown steps ---------------------------------------------------------

    def announce_stopping(self) -> None:
        sd_notify("STOPPING=1")
        self.watchdog.stop()

    def release_ptt(self) -> None:
        """Unkey the transmitters. Idempotent, quick, and the first thing shutdown does: Docker sends SIGKILL about
        10 s after SIGTERM, and a stop that hangs behind an integration must not leave the line keyed. Each radio
        is tried even if the other one's release fails."""
        for name, release in (("live", self.live_audio.release_ptt), ("link_radio", self.link_radio.release_ptt)):
            try:
                release()
            except Exception:  # noqa: BLE001 - the other radio and the rest of shutdown still go ahead
                log_event(
                    _core_logger, logging.ERROR, "core.shutdown_step_failed", "couldn't release PTT at shutdown",
                    target=f"ptt_{name}", error_code="core.shutdown", exc_info=True, facts_in_text=False,
                )

    async def stop_devices(self) -> None:
        """PTT first, then each integration on its own timeout, then the audio engines close."""
        self.release_ptt()
        for name, stop in (
            ("gpio", self.gpio.stop),
            ("tx_fan", self.tx_fan.stop),
            ("allstar", self.allstar_node.stop),
            ("dmr", self.dmr_audio.stop),
            ("autopatch", self.autopatch.stop),
        ):
            await self._stop_step(name, stop())
        for name, shutdown in (
            ("live_audio", self.live_audio.shutdown),
            ("monitor_receiver", self.monitor_receiver.shutdown),
            ("link_radio", self.link_radio.shutdown),
        ):
            try:
                shutdown()
            except Exception:  # noqa: BLE001
                self._shutdown_failed(name, "failed")

    async def _stop_step(self, name: str, work) -> None:
        """One integration's stop: bounded, and whatever goes wrong is logged and does not reach the next one."""
        try:
            await asyncio.wait_for(work, INTEGRATION_STOP_SECONDS)
        except asyncio.TimeoutError:
            self._shutdown_failed(name, "timed_out")
        except Exception:  # noqa: BLE001
            self._shutdown_failed(name, "failed")

    @staticmethod
    def _shutdown_failed(name: str, reason: str) -> None:
        log_event(
            _core_logger, logging.ERROR, "core.shutdown_step_failed", f"{name} did not stop cleanly ({reason})",
            target=name, reason=reason, error_code="core.shutdown", exc_info=reason == "failed", facts_in_text=False,
        )

    async def stop_tts(self) -> None:
        loop = asyncio.get_running_loop()
        if self._piper_watch is not None:
            self._piper_watch.cancel()
            self._piper_watch = None
        if self.kokoro_service is not None:
            await loop.run_in_executor(None, self.kokoro_service.stop)
            self.kokoro_service = None
        if self.piper_service is not None:
            await loop.run_in_executor(None, self.piper_service.stop)
            self.piper_service = None


class SplitCoreRuntime:
    """Stands in for `CoreRuntime` when the core runs in its own process (Stage 3 slice 7c-7, #229).

    The API's lifespan calls the same steps in the same order. Every one that starts or stops the
    radio, audio, PTT or text-to-speech does nothing here, because the core owns them. The two that
    matter are `start_io`, which starts following the core, and `stop_devices`, which stops following
    it. Nothing here touches a device."""

    def __init__(self, remote) -> None:
        self.remote = remote
        self.kokoro_service = None
        self.piper_service = None
        self.last_tick: list = [None]

    async def render_and_queue(self, clip: str, *, on_linked_radio: bool = False) -> None:
        await self.remote.render_and_queue(clip, on_linked_radio=on_linked_radio)

    def start_fan(self) -> None: ...
    def start_tick(self, tasks: list) -> None: ...
    def start_announcements(self, tasks: list) -> None: ...
    async def start_tts(self) -> None: ...
    def warm_renderer(self, loop) -> None: ...
    def start_long_id(self, tasks: list) -> None: ...
    async def start_audio_and_links(self, loop, tasks: list) -> None: ...
    def notify_ready(self) -> None: ...
    def announce_stopping(self) -> None: ...
    async def stop_tts(self) -> None: ...

    def start_io(self) -> None:
        self.remote.start()  # follow the core; it may not be up yet, and that is fine

    async def stop_devices(self) -> None:
        await self.remote.stop()
