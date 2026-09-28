"""Autopatch: phone calls through the Asterisk sidecar, bridged to the repeater.

A call goes like this:

  1. `dial(number)` checks the number against the allowed patterns, keys
     the transmitter (the controller's PATCH state), says the number back
     and plays ringback, while Asterisk places the call over AMI:
     Originate with Channel=<dial string>, Application=AudioSocket,
     Data=<call id>,<our address>, Async, and ChannelId=<call id> so the
     channel can be found (and hung up) while it rings.
  2. When the far end answers, Asterisk runs AudioSocket, which connects to
     our TCP server and names the call by its UUID. From then on 20 ms
     frames of 8 kHz audio flow both ways: received radio audio to the
     phone, phone audio out the transmitter.
  3. It ends on the hangup code, the far end hanging up, or the time limit.
     We hang up by sending AudioSocket's hangup frame once connected, or an
     AMI Hangup of the ringing channel.

Only app_audiosocket from the dialplan is used -- not app_rpt -- so none of
this depends on the app_rpt audio bridging issue noted in the README.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import asdict, dataclass
from typing import Callable, NamedTuple, Optional

import numpy as np

from audio_io.patch import PatchAudio
from audio_io.resample import StreamResampler
from controller.autopatch import number_allowed
from controller.state_machine import RepeaterConfig
from link.ami_client import AMIClient, AMIMessage
from link.audiosocket import FrameType, encode_frame, read_frame_async
from playout.renderer import TTS_PREFIX

from .service import RepeaterService

_logger = logging.getLogger("moreopenrepeater.autopatch")

PHONE_RATE = 8000  # AudioSocket's signed-linear audio
FRAME_SECONDS = 0.02
AMI_TIMEOUT = 10.0
ANSWER_TO_CONNECT_TIMEOUT = 10.0  # answered -> AudioSocket connects back
TIME_WARNING_SECONDS = 30.0
UUID_TIMEOUT = 5.0


class PatchSettings(NamedTuple):
    """Deployment wiring, from the environment like the other AMI settings."""

    ami_host: str
    ami_port: int
    ami_username: str
    ami_secret: str
    listen_host: str
    listen_port: int
    address: str  # host:port that Asterisk connects to for AudioSocket


def patch_settings_from_env(env: dict) -> Optional[PatchSettings]:
    host = env.get("MOREOPENREPEATER_AMI_HOST")
    if not host:
        return None
    listen = env.get("MOREOPENREPEATER_AUDIOSOCKET_LISTEN", "127.0.0.1:9092")
    listen_host, _, listen_port = listen.rpartition(":")
    return PatchSettings(
        ami_host=host,
        ami_port=int(env.get("MOREOPENREPEATER_AMI_PORT", "5038")),
        ami_username=env.get("MOREOPENREPEATER_AMI_USER", "admin"),
        ami_secret=env.get("MOREOPENREPEATER_AMI_SECRET", ""),
        listen_host=listen_host or "127.0.0.1",
        listen_port=int(listen_port),
        address=env.get("MOREOPENREPEATER_AUDIOSOCKET_ADDRESS", listen),
    )


# OriginateResponse's Reason is the last control frame the channel saw
# (Asterisk's AST_CONTROL_* numbers).
_FAILURES = {
    "1": ("no answer", "The call was not answered."),  # hung up while ringing
    "3": ("no answer", "The call was not answered."),  # still ringing at the timeout
    "5": ("busy", "The line is busy."),
    "8": ("couldn't be completed", "The call could not be completed."),  # congestion
}
_UNCOMPLETED = ("couldn't be completed", "The call could not be completed.")


class CallFailed(Exception):
    def __init__(self, result: str, speech: str) -> None:
        super().__init__(result)
        self.result = result
        self.speech = speech


@dataclass
class CallRecord:
    number: str
    actor: str
    started_at: float
    state: str = "dialing"  # dialing | connected | ended
    connected_at: Optional[float] = None
    ended_at: Optional[float] = None
    result: str = ""


def ringback(rate: int) -> np.ndarray:
    """North American ringback: 440 + 480 Hz, two seconds on, four off."""
    t = np.arange(2 * rate) / rate
    tone = 0.12 * (np.sin(2 * np.pi * 440 * t) + np.sin(2 * np.pi * 480 * t))
    return np.concatenate([tone, np.zeros(4 * rate)]).astype(np.float32)


def _to_pcm(samples: np.ndarray) -> bytes:
    return (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2").tobytes()


def _from_pcm(payload: bytes) -> np.ndarray:
    return np.frombuffer(payload, dtype="<i2").astype(np.float32) / 32768


class Autopatch:
    def __init__(
        self,
        service: RepeaterService,
        settings: Optional[PatchSettings],
        ami_factory: Callable[..., AMIClient] = AMIClient,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._service = service
        self.settings = settings
        self._ami_factory = ami_factory
        self._clock = clock
        self._server: Optional[asyncio.AbstractServer] = None
        self._waiting: dict[str, "asyncio.Future[tuple]"] = {}
        self._task: Optional[asyncio.Task] = None
        self._hangup_reason: Optional[str] = None
        self.error: Optional[str] = None
        self.call: Optional[CallRecord] = None
        self.last_call: Optional[CallRecord] = None
        service.autopatch = self
        service.add_config_listener(self._config_changed)

    @property
    def rate(self) -> int:
        return self._service.renderer.sample_rate if self._service.renderer else 16000

    async def start(self) -> None:
        if self.settings is None:
            return
        host, port = self.settings.listen_host, self.settings.listen_port
        try:
            self._server = await asyncio.start_server(self._accept, host, port)
        except OSError as error:
            self.error = f"couldn't listen for AudioSocket on {host}:{port}: {error}"
            _logger.error("%s", self.error)
            return
        _logger.info("AudioSocket server listening on %s:%s", host, port)

    @property
    def listen_port(self) -> Optional[int]:
        """The bound port (useful when configured as 0)."""
        if self._server is None or not self._server.sockets:
            return None
        return self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        self.hangup("the server shut down")
        if self._task is not None:
            await asyncio.gather(self._task, return_exceptions=True)
        if self._server is not None:
            self._server.close()

    def status(self) -> dict:
        config = self._service.config
        return {
            "available": self._server is not None,
            "configured": self.settings is not None,
            "error": self.error,
            "enabled": config.autopatch_enabled,
            "call": asdict(self.call) if self.call else None,
            "last_call": asdict(self.last_call) if self.last_call else None,
        }

    def dial(self, number: str, actor: str) -> Optional[str]:
        """Start a call; returns why not, if it can't."""
        config = self._service.config
        if not config.autopatch_enabled:
            error = "Autopatch is turned off."
        elif self.settings is None or self._server is None:
            error = "Autopatch is not available."
        elif not config.transmitter_enabled:
            error = "The transmitter is off."
        elif self.call is not None:
            error = "A call is already in progress."
        elif not number_allowed(number, config.autopatch_allowed, config.autopatch_blocked):
            error = "That number is not allowed."
        else:
            error = None
        if error is not None:
            _logger.info("autopatch call to %s refused: %s", number, error)
            self._audit(actor, "Autopatch refused", f"{number}: {error}")
            if actor == "DTMF":
                self._service.speak(TTS_PREFIX + error)
            return error
        self.call = CallRecord(number=number, actor=actor, started_at=self._clock())
        self._hangup_reason = None
        patch = PatchAudio(self.rate)
        self._service.begin_patch()
        self._set_patch(patch)
        self._task = asyncio.create_task(self._run(self.call, config, patch))
        return None

    def hangup(self, reason: str) -> None:
        if self._task is None or self._task.done() or self._hangup_reason is not None:
            return
        self._hangup_reason = reason
        # A task cancelled before its first step never runs, so it couldn't clean up.
        asyncio.get_running_loop().call_soon(self._task.cancel)

    # -- a call --------------------------------------------------------------

    async def _run(self, call: CallRecord, config: RepeaterConfig, patch: PatchAudio) -> None:
        loop = asyncio.get_running_loop()
        call_id = str(uuid.uuid4())
        answered: "asyncio.Future[tuple]" = loop.create_future()
        originated: "asyncio.Future[AMIMessage]" = loop.create_future()
        self._waiting[call_id] = answered
        channel: dict[str, Optional[str]] = {"name": None}
        dialing_audio = asyncio.create_task(self._dialing_audio(patch, call.number, config))
        ami: Optional[AMIClient] = None
        watcher: Optional[asyncio.Task] = None
        connection: Optional[tuple] = None
        speech = "Autopatch ended."
        _logger.info("autopatch dialing %s (%s)", call.number, call.actor)
        self._audit(call.actor, "Autopatch call", call.number)
        try:
            assert self.settings is not None
            ami = self._ami_factory(
                self.settings.ami_host, self.settings.ami_port, self.settings.ami_username, self.settings.ami_secret
            )
            await asyncio.wait_for(ami.connect(), AMI_TIMEOUT)
            watcher = asyncio.create_task(self._watch(ami, call_id, originated, channel))
            action: AMIMessage = {
                "Action": "Originate",
                "ActionID": call_id,
                "Channel": config.autopatch_dial_string.replace("{number}", call.number),
                "Application": "AudioSocket",
                "Data": f"{call_id},{self.settings.address}",
                "Timeout": str(int(config.autopatch_ring_seconds * 1000)),
                "Async": "true",
                "ChannelId": call_id,
            }
            if config.autopatch_caller_id:
                action["CallerID"] = config.autopatch_caller_id
            response = await asyncio.wait_for(ami.send_action(action), AMI_TIMEOUT)
            if response.get("Response") != "Success":
                _logger.error("Asterisk refused the call: %s", response.get("Message"))
                raise CallFailed(*_UNCOMPLETED)
            result = await asyncio.wait_for(originated, config.autopatch_ring_seconds + AMI_TIMEOUT)
            if result.get("Response") != "Success":
                raise CallFailed(*_FAILURES.get(str(result.get("Reason")), _UNCOMPLETED))
            connection = await asyncio.wait_for(answered, ANSWER_TO_CONNECT_TIMEOUT)
            dialing_audio.cancel()
            call.state = "connected"
            call.connected_at = self._clock()
            _logger.info("autopatch call to %s connected", call.number)
            reader, writer, _done = connection
            call.result = await self._bridge(reader, writer, patch, config.autopatch_max_call_seconds)
            if call.result == "time limit reached":
                speech = "Time limit reached. Autopatch ended."
        except CallFailed as failure:
            call.result, speech = failure.result, failure.speech
        except asyncio.CancelledError:
            call.result = self._hangup_reason or "hung up"
            if call.state == "dialing":
                speech = "Autopatch cancelled."
        except (OSError, ConnectionError, ValueError, asyncio.TimeoutError, asyncio.IncompleteReadError) as error:
            _logger.error("autopatch call to %s failed: %r", call.number, error)
            call.result, speech = _UNCOMPLETED
        finally:
            dialing_audio.cancel()
            self._waiting.pop(call_id, None)
            if connection is not None:
                await self._close_socket(connection)
            elif ami is not None and channel["name"] and (originated.cancelled() or not originated.done()):
                await self._ami_hangup(ami, channel["name"])  # still ringing
            if watcher is not None:
                watcher.cancel()
            if ami is not None:
                try:
                    await ami.close()
                except OSError:
                    pass
            self._set_patch(None)
            call.state = "ended"
            call.ended_at = self._clock()
            self.call = None
            self.last_call = call
            talk_time = f", {round(call.ended_at - call.connected_at)}s" if call.connected_at else ""
            _logger.info("autopatch call to %s ended: %s", call.number, call.result)
            self._audit(call.actor, "Autopatch ended", f"{call.number}: {call.result}{talk_time}")
            self._service.end_patch()
            self._service.speak(TTS_PREFIX + speech)

    async def _watch(self, ami: AMIClient, call_id: str, originated: "asyncio.Future[AMIMessage]", channel: dict) -> None:
        async for event in ami.events():
            if event.get("Uniqueid") == call_id and isinstance(event.get("Channel"), str):
                channel["name"] = event["Channel"]
            if event.get("Event") == "OriginateResponse" and event.get("ActionID") == call_id and not originated.done():
                originated.set_result(event)
        if not originated.done():
            originated.set_exception(ConnectionError("AMI connection closed"))

    async def _ami_hangup(self, ami: AMIClient, channel: str) -> None:
        try:
            await asyncio.wait_for(ami.send_action({"Action": "Hangup", "Channel": channel}), AMI_TIMEOUT)
        except (OSError, ConnectionError, asyncio.TimeoutError):
            _logger.warning("couldn't hang up %s", channel)

    async def _close_socket(self, connection: tuple) -> None:
        _reader, writer, done = connection
        try:
            writer.write(encode_frame(FrameType.HANGUP))
            await writer.drain()
            writer.close()
        except (OSError, ConnectionError):
            pass
        if not done.done():
            done.set_result(None)

    def _set_patch(self, patch: Optional[PatchAudio]) -> None:
        output = self._service.audio_output
        if output is not None:
            output.set_patch(patch)

    async def _dialing_audio(self, patch: PatchAudio, number: str, config: RepeaterConfig) -> None:
        """What the repeater hears until the far end answers: the number read
        back, then ringback."""
        renderer = self._service.renderer
        if renderer is not None:
            text = TTS_PREFIX + "Dialing " + " ".join(number)
            try:
                spoken = await asyncio.get_running_loop().run_in_executor(None, renderer.render, text, config)
                await self._feed(patch, spoken)
            except Exception:
                _logger.warning("couldn't render the dialing announcement", exc_info=True)
        ring = ringback(self.rate)
        while True:
            await self._feed(patch, ring)

    async def _feed(self, patch: PatchAudio, samples: np.ndarray) -> None:
        """Add local audio to the phone side in real time, as if the phone sent it."""
        loop = asyncio.get_running_loop()
        step = int(self.rate * FRAME_SECONDS)
        next_at = loop.time()
        for start in range(0, len(samples), step):
            patch.add_phone(samples[start : start + step])
            next_at += FRAME_SECONDS
            await asyncio.sleep(max(0.0, next_at - loop.time()))

    async def _bridge(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, patch: PatchAudio, limit: float) -> str:
        sender = asyncio.create_task(self._send_radio(writer, patch))
        receiver = asyncio.create_task(self._receive_phone(reader, patch))
        loop = asyncio.get_running_loop()
        warning = (
            loop.call_later(limit - TIME_WARNING_SECONDS, self._time_warning)
            if limit > 2 * TIME_WARNING_SECONDS
            else None
        )
        try:
            done, _ = await asyncio.wait({sender, receiver}, timeout=limit, return_when=asyncio.FIRST_COMPLETED)
            if not done:
                return "time limit reached"
            for task in done:
                if task.exception() is not None:
                    _logger.info("AudioSocket connection ended: %r", task.exception())
            return "the other party hung up"
        finally:
            sender.cancel()
            receiver.cancel()
            if warning is not None:
                warning.cancel()

    def _time_warning(self) -> None:
        output = self._service.audio_output
        if output is not None:
            output.play(TTS_PREFIX + f"{int(TIME_WARNING_SECONDS)} seconds remaining.")

    async def _send_radio(self, writer: asyncio.StreamWriter, patch: PatchAudio) -> None:
        """One frame of received radio audio every 20 ms. Silence when there's
        none, which also keeps AudioSocket's 2-second inactivity timer happy."""
        loop = asyncio.get_running_loop()
        resampler = StreamResampler(self.rate, PHONE_RATE)
        silence = np.zeros(int(self.rate * FRAME_SECONDS), dtype=np.float32)
        frame = int(PHONE_RATE * FRAME_SECONDS)
        pending = np.zeros(0, dtype=np.float32)
        next_at = loop.time()
        while True:
            block = patch.take_radio()
            pending = np.concatenate([pending, resampler.process(block if block is not None else silence)])
            while len(pending) >= frame:
                writer.write(encode_frame(FrameType.AUDIO, _to_pcm(pending[:frame])))
                pending = pending[frame:]
            await writer.drain()
            next_at += FRAME_SECONDS
            await asyncio.sleep(max(0.0, next_at - loop.time()))

    async def _receive_phone(self, reader: asyncio.StreamReader, patch: PatchAudio) -> None:
        resampler = StreamResampler(PHONE_RATE, self.rate)
        while True:
            try:
                frame = await read_frame_async(reader)
            except asyncio.IncompleteReadError:
                return
            if frame.type == FrameType.AUDIO:
                patch.add_phone(resampler.process(_from_pcm(frame.payload)))
            elif frame.type in (FrameType.HANGUP, FrameType.ERROR):
                return

    # -- AudioSocket server --------------------------------------------------

    async def _accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            frame = await asyncio.wait_for(read_frame_async(reader), UUID_TIMEOUT)
            call_id = str(uuid.UUID(bytes=frame.payload)) if frame.type == FrameType.UUID else None
        except (OSError, ValueError, asyncio.TimeoutError, asyncio.IncompleteReadError):
            call_id = None
        waiting = self._waiting.get(call_id) if call_id else None
        if waiting is None or waiting.done():
            _logger.warning("AudioSocket connection for unknown call %s; hanging up", call_id)
            writer.write(encode_frame(FrameType.HANGUP))
            writer.close()
            return
        done: "asyncio.Future[None]" = asyncio.get_running_loop().create_future()
        waiting.set_result((reader, writer, done))
        await done  # the call owns the connection until it ends

    def _config_changed(self, config: RepeaterConfig) -> None:
        if not config.autopatch_enabled:
            self.hangup("autopatch was turned off")
        elif not config.transmitter_enabled:
            self.hangup("the transmitter was turned off")

    def _audit(self, actor: str, action: str, detail: str) -> None:
        if self._service.audit_hook is not None:
            self._service.audit_hook(actor, action, detail)
