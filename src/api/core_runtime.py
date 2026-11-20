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
EXTERNAL_PIPER_WAIT_SECONDS = 60.0  # how long the core waits for Piper's container at start; then it starts anyway

_tts_logger = logging.getLogger("moreopenrepeater.tts")
_announce_logger = logging.getLogger("moreopenrepeater.announcements")


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
        """Start the Kokoro or Piper server and wait for it. Audio and PTT are
        not live until this returns (inventory finding F10)."""
        loop = asyncio.get_running_loop()
        kokoro_service = kokoro_service_for(self.renderer)
        if kokoro_service is not None:
            try:
                await loop.run_in_executor(None, kokoro_service.start_and_wait)
                self.kokoro_service = kokoro_service
            except KokoroServiceError:
                _tts_logger.exception("Kokoro service failed to become ready; voice IDs fall back to CW until it works")
                await loop.run_in_executor(None, kokoro_service.stop)
        await self._wait_for_external_piper(loop)
        piper_service = piper_service_for(self.renderer, voice=self.service.config.tts_voice)
        if piper_service is not None:
            try:
                await loop.run_in_executor(None, piper_service.start_and_wait)
                self.piper_service = piper_service
            except PiperServiceError:
                _tts_logger.exception("Piper service failed to become ready; voice IDs use CLI/espeak until it works")
                await loop.run_in_executor(None, piper_service.stop)

    async def _wait_for_external_piper(self, loop: asyncio.AbstractEventLoop) -> None:
        """Piper in its own container: give it a bounded time to be up before audio goes live, so the first long ID is not
        spoken by the fallback voice. If it is not up in time the repeater starts anyway (voice IDs fall back until it is).
        Nothing is started or stopped here: it is not this process's."""
        from playout.tts import piper_service_external, piper_service_url

        tts = getattr(self.renderer, "tts", None)
        if tts is None or getattr(tts, "name", None) != "piper" or not piper_service_external():
            return
        seconds = EXTERNAL_PIPER_WAIT_SECONDS
        if await loop.run_in_executor(None, wait_until_ready, piper_service_url(), seconds):
            _tts_logger.info("the Piper service is ready")
        else:
            _tts_logger.warning(
                "the Piper service did not answer in %gs; voice IDs use the fallback voice until it does", seconds
            )

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

    async def stop_devices(self) -> None:
        await self.gpio.stop()
        await self.tx_fan.stop()
        await self.allstar_node.stop()
        await self.dmr_audio.stop()
        await self.autopatch.stop()
        self.live_audio.shutdown()
        self.monitor_receiver.shutdown()
        self.link_radio.shutdown()

    async def stop_tts(self) -> None:
        loop = asyncio.get_running_loop()
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
