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
  3. DTMF keyed on the radio during the call is muted from the audio like
     any other DTMF, so each decoded digit goes to the call's channel with
     an AMI PlayDTMF instead (out of band, RFC 4733 on a SIP trunk).
  4. It ends on the hangup code, the far end hanging up, or the time limit.
     We hang up by sending AudioSocket's hangup frame once connected, or an
     AMI Hangup of the ringing channel.

A call in arrives the other way round: Asterisk's `mor-incoming` context
(see sip_trunk) answers and connects to our server under a UUID we've never
seen. If AMI shows a channel in that context running AudioSocket with that
UUID, it's a real call: the caller keys in the access code, then the
repeater announces the call and bridges it like one going out.

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
from controller.autopatch import format_number, number_allowed
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
INCOMING_CONTEXT = "mor-incoming"
PIN_TRIES = 3
PIN_DIGIT_TIMEOUT = 10.0
DTMF_DURATION_MS = 200


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
# A SIP call that fails reports Reason 0; the channel's Hangup event carries
# the Q.850 cause instead (17 user busy, 18 no user responding, 19 no answer).
_CAUSES = {"17": _FAILURES["5"], "18": _FAILURES["3"], "19": _FAILURES["3"]}
HANGUP_CAUSE_WAIT = 1.0


class CallFailed(Exception):
    def __init__(self, result: str, speech: str) -> None:
        super().__init__(result)
        self.result = result
        self.speech = speech


@dataclass
class CallRecord:
    number: str  # the caller's, for a call in ("" when unknown)
    actor: str
    started_at: float
    state: str = "dialing"  # dialing | connected | ended
    connected_at: Optional[float] = None
    ended_at: Optional[float] = None
    result: str = ""
    direction: str = "outgoing"  # outgoing | incoming

    @property
    def who(self) -> str:
        if self.direction == "outgoing":
            return self.number
        return f"a call from {self.number or 'an unknown number'}"


def ringback(rate: int) -> np.ndarray:
    """North American ringback: 440 + 480 Hz, two seconds on, four off."""
    t = np.arange(2 * rate) / rate
    tone = 0.12 * (np.sin(2 * np.pi * 440 * t) + np.sin(2 * np.pi * 480 * t))
    return np.concatenate([tone, np.zeros(4 * rate)]).astype(np.float32)


def _to_pcm(samples: np.ndarray) -> bytes:
    return (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2").tobytes()


def _from_pcm(payload: bytes) -> np.ndarray:
    return np.frombuffer(payload, dtype="<i2").astype(np.float32) / 32768


async def _close_quietly(ami: AMIClient) -> None:
    try:
        await ami.close()
    except OSError:
        pass


async def ami_list(ami: AMIClient, fields: AMIMessage) -> Optional[list[AMIMessage]]:
    """A list action's events, or None if Asterisk has nothing to list."""
    action_id = f"mor-list-{uuid.uuid4().hex}"
    response = await asyncio.wait_for(ami.send_action({**fields, "ActionID": action_id}), AMI_TIMEOUT)
    if response.get("Response") != "Success":
        return None
    items: list[AMIMessage] = []

    async def collect() -> None:
        async for event in ami.events():
            if event.get("ActionID") != action_id:
                continue
            if event.get("EventList") == "Complete":
                return
            items.append(event)

    await asyncio.wait_for(collect(), AMI_TIMEOUT)
    return items


class PhoneLine:
    """One AudioSocket connection: a 20 ms frame of 8 kHz audio each way
    (silence when there's nothing to send, which also keeps AudioSocket's
    2-second inactivity timer happy), and the caller's keypresses.

    Until `connect`, the caller hears only what `say` plays; after, the
    patch carries audio both ways."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, rate: int) -> None:
        self._reader = reader
        self._writer = writer
        self._rate = rate
        self._patch: Optional[PatchAudio] = None
        self._prompt = np.zeros(0, dtype=np.float32)  # at PHONE_RATE
        self._prompt_done: Optional["asyncio.Future[None]"] = None
        self._digits: "asyncio.Queue[str]" = asyncio.Queue()
        self._tasks: list[asyncio.Task] = []

    def start(self) -> None:
        self._tasks = [asyncio.create_task(self._send()), asyncio.create_task(self._receive())]

    def connect(self, patch: PatchAudio) -> None:
        self._patch = patch

    async def wait_closed(self) -> None:
        """Until the far end hangs up or the connection drops."""
        done, _ = await asyncio.wait(self._tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            if not task.cancelled() and task.exception() is not None:
                _logger.info("AudioSocket connection ended: %r", task.exception())

    @property
    def closed(self) -> bool:
        return any(task.done() for task in self._tasks)

    async def say(self, samples: np.ndarray) -> None:
        """Play `samples` (at the processing rate) to the caller, and wait for them to finish."""
        resampler = StreamResampler(self._rate, PHONE_RATE)
        tail = np.zeros(self._rate // 10, dtype=np.float32)  # flushes the resampler's filter
        self._prompt = np.concatenate([self._prompt, resampler.process(samples), resampler.process(tail)])
        if self._prompt_done is None or self._prompt_done.done():
            self._prompt_done = asyncio.get_running_loop().create_future()
        closed = asyncio.ensure_future(self.wait_closed())
        try:
            await asyncio.wait({self._prompt_done, closed}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            closed.cancel()

    async def read_digits(self, count: int, timeout: float) -> Optional[str]:
        """Up to `count` keypresses, ending early on #; None if the caller
        hangs up or stops pressing keys."""
        digits = ""
        closed = asyncio.ensure_future(self.wait_closed())
        try:
            while len(digits) < count:
                digit = asyncio.ensure_future(self._digits.get())
                done, _ = await asyncio.wait({digit, closed}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
                if digit not in done:
                    digit.cancel()
                    return None
                if digit.result() == "#":
                    break
                digits += digit.result()
        finally:
            closed.cancel()
        return digits

    def clear_digits(self) -> None:
        while not self._digits.empty():
            self._digits.get_nowait()

    async def close(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        try:
            self._writer.write(encode_frame(FrameType.HANGUP))
            await self._writer.drain()
            self._writer.close()
        except (OSError, ConnectionError):
            pass

    async def _send(self) -> None:
        loop = asyncio.get_running_loop()
        resampler = StreamResampler(self._rate, PHONE_RATE)
        silence = np.zeros(int(self._rate * FRAME_SECONDS), dtype=np.float32)
        frame = int(PHONE_RATE * FRAME_SECONDS)
        radio = np.zeros(0, dtype=np.float32)
        next_at = loop.time()
        while True:
            frames: list[np.ndarray] = []
            if self._patch is None:
                frames.append(np.zeros(frame, dtype=np.float32))
            else:
                block = self._patch.take_radio()
                radio = np.concatenate([radio, resampler.process(block if block is not None else silence)])
                while len(radio) >= frame:
                    frames.append(radio[:frame])
                    radio = radio[frame:]
            for out in frames:
                if len(self._prompt):
                    out = np.zeros(frame, dtype=np.float32)
                    piece, self._prompt = self._prompt[:frame], self._prompt[frame:]
                    out[: len(piece)] = piece
                    if not len(self._prompt) and self._prompt_done is not None and not self._prompt_done.done():
                        self._prompt_done.set_result(None)
                self._writer.write(encode_frame(FrameType.AUDIO, _to_pcm(out)))
            await self._writer.drain()
            next_at += FRAME_SECONDS
            await asyncio.sleep(max(0.0, next_at - loop.time()))

    async def _receive(self) -> None:
        resampler = StreamResampler(PHONE_RATE, self._rate)
        while True:
            try:
                frame = await read_frame_async(self._reader)
            except asyncio.IncompleteReadError:
                return
            if frame.type == FrameType.AUDIO:
                if self._patch is not None:
                    self._patch.add_phone(resampler.process(_from_pcm(frame.payload)))
            elif frame.type == FrameType.DTMF:
                self._digits.put_nowait(frame.payload.decode("ascii", "replace")[:1])
            elif frame.type in (FrameType.HANGUP, FrameType.ERROR):
                return


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
        self._digits: Optional["asyncio.Queue[Optional[str]]"] = None  # while a call is connected
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
            error = self._service.held_reason("autopatch") or "Autopatch is turned off."
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

    def send_digit(self, digit: str) -> None:
        """A digit keyed on the radio, for the far end of a connected call."""
        if self._digits is not None:
            self._digits.put_nowait(digit)

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
        channel: dict[str, Optional[str]] = {"name": None, "cause": None}
        hung_up = asyncio.Event()
        dialing_audio = asyncio.create_task(self._dialing_audio(patch, call.number, config))
        ami: Optional[AMIClient] = None
        watcher: Optional[asyncio.Task] = None
        sender: Optional[asyncio.Task] = None
        connection: Optional[tuple] = None
        line: Optional[PhoneLine] = None
        speech = "Autopatch ended."
        _logger.info("autopatch dialing %s (%s)", call.number, call.actor)
        self._audit(call.actor, "Autopatch call", call.number)
        try:
            assert self.settings is not None
            ami = self._ami_factory(
                self.settings.ami_host, self.settings.ami_port, self.settings.ami_username, self.settings.ami_secret
            )
            await asyncio.wait_for(ami.connect(), AMI_TIMEOUT)
            watcher = asyncio.create_task(self._watch(ami, call_id, originated, channel, hung_up))
            action: AMIMessage = {
                "Action": "Originate",
                "ActionID": call_id,
                "Channel": config.autopatch_dial_string.replace(
                    "{number}", format_number(call.number, config.autopatch_ten_digit_prefix)
                ),
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
                failure = _FAILURES.get(str(result.get("Reason")))
                if failure is None:
                    try:
                        await asyncio.wait_for(hung_up.wait(), HANGUP_CAUSE_WAIT)
                    except asyncio.TimeoutError:
                        pass
                    failure = _CAUSES.get(str(channel["cause"]), _UNCOMPLETED)
                raise CallFailed(*failure)
            connection = await asyncio.wait_for(answered, ANSWER_TO_CONNECT_TIMEOUT)
            dialing_audio.cancel()
            call.state = "connected"
            call.connected_at = self._clock()
            _logger.info("autopatch call to %s connected", call.number)
            reader, writer, _done = connection
            line = PhoneLine(reader, writer, self.rate)
            line.connect(patch)
            line.start()
            sender = self._start_digits(channel["name"] or call_id, ami)
            call.result = await self._talk(line, config.autopatch_max_call_seconds)
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
            await self._stop_digits(sender)
            self._waiting.pop(call_id, None)
            if connection is not None:
                await self._close_socket(connection, line)
            elif ami is not None and channel["name"] and (originated.cancelled() or not originated.done()):
                await self._ami_hangup(ami, channel["name"])  # still ringing
            if watcher is not None:
                watcher.cancel()
            if ami is not None:
                try:
                    await ami.close()
                except OSError:
                    pass
            self._finish(call, speech)

    def _finish(self, call: CallRecord, speech: str) -> None:
        self._set_patch(None)
        call.state = "ended"
        call.ended_at = self._clock()
        self.call = None
        self.last_call = call
        talk_time = f", {round(call.ended_at - call.connected_at)}s" if call.connected_at else ""
        _logger.info("autopatch %s ended: %s", call.who, call.result)
        self._audit(call.actor, "Autopatch ended", f"{call.who}: {call.result}{talk_time}")
        self._service.end_patch()
        self._service.speak(TTS_PREFIX + speech)

    async def _watch(
        self,
        ami: AMIClient,
        call_id: str,
        originated: "asyncio.Future[AMIMessage]",
        channel: dict,
        hung_up: asyncio.Event,
    ) -> None:
        async for event in ami.events():
            if event.get("Uniqueid") == call_id and isinstance(event.get("Channel"), str):
                channel["name"] = event["Channel"]
                if event.get("Event") == "Hangup":
                    channel["cause"] = event.get("Cause")
                    hung_up.set()
            if event.get("Event") == "OriginateResponse" and event.get("ActionID") == call_id and not originated.done():
                originated.set_result(event)
        if not originated.done():
            originated.set_exception(ConnectionError("AMI connection closed"))

    async def _ami_hangup(self, ami: AMIClient, channel: str) -> None:
        try:
            await asyncio.wait_for(ami.send_action({"Action": "Hangup", "Channel": channel}), AMI_TIMEOUT)
        except (OSError, ConnectionError, asyncio.TimeoutError):
            _logger.warning("couldn't hang up %s", channel)

    def _start_digits(self, channel: str, ami: Optional[AMIClient]) -> asyncio.Task:
        self._digits = asyncio.Queue()
        return asyncio.create_task(self._send_digits(self._digits, channel, ami))

    async def _stop_digits(self, sender: Optional[asyncio.Task]) -> None:
        digits, self._digits = self._digits, None
        if sender is None:
            return
        if digits is not None:
            digits.put_nowait(None)  # Python 3.11's wait_for can swallow the cancel
        sender.cancel()
        await asyncio.wait({sender}, timeout=AMI_TIMEOUT)

    async def _send_digits(self, digits: "asyncio.Queue[Optional[str]]", channel: str, ami: Optional[AMIClient]) -> None:
        """Each queued digit to `channel`, in order, until None. Without the
        call's own AMI connection (a call in), one is opened at the first digit."""
        own: Optional[AMIClient] = None
        try:
            while (digit := await digits.get()) is not None:
                try:
                    if ami is None:
                        assert self.settings is not None
                        s = self.settings
                        own = ami = self._ami_factory(s.ami_host, s.ami_port, s.ami_username, s.ami_secret)
                        await asyncio.wait_for(ami.connect(), AMI_TIMEOUT)
                    action = {"Action": "PlayDTMF", "Channel": channel, "Digit": digit, "Duration": str(DTMF_DURATION_MS)}
                    response = await asyncio.wait_for(ami.send_action(action), AMI_TIMEOUT)
                    if response.get("Response") != "Success":
                        _logger.warning("Asterisk didn't send DTMF %r to %s: %s", digit, channel, response.get("Message"))
                except (OSError, ConnectionError, asyncio.TimeoutError) as error:
                    _logger.warning("couldn't send DTMF %r to %s: %r", digit, channel, error)
                    if own is not None:
                        await _close_quietly(own)
                        ami = own = None
        finally:
            if own is not None:
                await _close_quietly(own)

    async def _close_socket(self, connection: tuple, line: Optional[PhoneLine]) -> None:
        _reader, writer, done = connection
        if line is not None:
            await line.close()
        else:
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
        await self._announce(patch, "Dialing " + " ".join(number), config)
        ring = ringback(self.rate)
        while True:
            await self._feed(patch, ring)

    async def _render(self, text: str, config: RepeaterConfig) -> Optional[np.ndarray]:
        renderer = self._service.renderer
        if renderer is None:
            return None
        try:
            return await asyncio.get_running_loop().run_in_executor(None, renderer.render, TTS_PREFIX + text, config)
        except Exception:
            _logger.warning("couldn't render %r", text, exc_info=True)
            return None

    async def _announce(self, patch: PatchAudio, text: str, config: RepeaterConfig) -> None:
        """Say `text` over the air, through the patch."""
        spoken = await self._render(text, config)
        if spoken is not None:
            await self._feed(patch, spoken)

    async def _say(self, line: PhoneLine, text: str) -> None:
        """Say `text` to the caller."""
        spoken = await self._render(text, self._service.config)
        if spoken is not None:
            await line.say(spoken)

    async def _feed(self, patch: PatchAudio, samples: np.ndarray) -> None:
        """Add local audio to the phone side in real time, as if the phone sent it."""
        loop = asyncio.get_running_loop()
        step = int(self.rate * FRAME_SECONDS)
        next_at = loop.time()
        for start in range(0, len(samples), step):
            patch.add_phone(samples[start : start + step])
            next_at += FRAME_SECONDS
            await asyncio.sleep(max(0.0, next_at - loop.time()))

    async def _talk(self, line: PhoneLine, limit: float) -> str:
        """Until the far end hangs up or the time limit; returns which."""
        loop = asyncio.get_running_loop()
        warning = (
            loop.call_later(limit - TIME_WARNING_SECONDS, self._time_warning)
            if limit > 2 * TIME_WARNING_SECONDS
            else None
        )
        try:
            await asyncio.wait_for(line.wait_closed(), limit)
            return "the other party hung up"
        except asyncio.TimeoutError:
            return "time limit reached"
        finally:
            if warning is not None:
                warning.cancel()

    def _time_warning(self) -> None:
        output = self._service.audio_output
        if output is not None:
            output.play(TTS_PREFIX + f"{int(TIME_WARNING_SECONDS)} seconds remaining.")

    # -- a call in -----------------------------------------------------------

    def _incoming_refusal(self) -> Optional[tuple[str, str]]:
        """Why a call in can't go on the air, and what the caller hears; None if it can."""
        config = self._service.config
        closed = "This repeater isn't taking phone calls. Goodbye."
        if not config.autopatch_enabled or not config.autopatch_incoming_enabled:
            return "calls in are turned off", closed
        if not config.autopatch_incoming_pin:
            return "no access code is set", closed
        if not config.transmitter_enabled:
            return "the transmitter is off", "The repeater can't take calls right now. Goodbye."
        if self.call is not None:
            return "a call is already in progress", "The repeater is on another call. Please try again later."
        return None

    async def _find_incoming(self, call_id: str) -> Optional[tuple[str, str]]:
        """The caller's number ("" if unknown) and the channel if `call_id` is a call in, else None."""
        if self.settings is None:
            return None
        s = self.settings
        ami = self._ami_factory(s.ami_host, s.ami_port, s.ami_username, s.ami_secret)
        try:
            await asyncio.wait_for(ami.connect(), AMI_TIMEOUT)
            channels = await ami_list(ami, {"Action": "CoreShowChannels"}) or []
        except (OSError, ConnectionError, asyncio.TimeoutError) as error:
            _logger.warning("couldn't look up AudioSocket call %s: %r", call_id, error)
            return None
        finally:
            try:
                await ami.close()
            except OSError:
                pass
        for channel in channels:
            if (
                channel.get("Context") == INCOMING_CONTEXT
                and channel.get("Application") == "AudioSocket"
                and str(channel.get("ApplicationData", "")).lower().startswith(call_id + ",")
            ):
                number = str(channel.get("CallerIDNum") or "")
                return ("" if number == "<unknown>" else number), str(channel.get("Channel") or call_id)
        return None

    async def _answer(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, caller: str, channel: str) -> None:
        line = PhoneLine(reader, writer, self.rate)
        line.start()
        try:
            if await self._screen(line, caller):
                await self._run_incoming(line, caller, channel)
        finally:
            await line.close()

    async def _screen(self, line: PhoneLine, caller: str) -> bool:
        """The access code, and whether the repeater can take the call; True to put it on the air."""
        who = f"call from {caller or 'an unknown number'}"
        _logger.info("autopatch %s", who)
        refusal = self._incoming_refusal()
        if refusal is None:
            pin = self._service.config.autopatch_incoming_pin
            entered: Optional[str] = None
            for attempt in range(PIN_TRIES):
                if attempt:
                    line.clear_digits()
                await self._say(line, "Enter the access code, then press pound." if attempt == 0 else "Wrong code. Try again.")
                entered = await line.read_digits(len(pin), PIN_DIGIT_TIMEOUT)
                if entered is None or entered == pin:
                    break
            if line.closed:
                self._audit("phone", "Autopatch refused", f"{who}: hung up before entering the access code")
                return False
            if entered is None:
                refusal = ("no access code entered", "Goodbye.")
            elif entered != pin:
                refusal = ("wrong access code", "Wrong code. Goodbye.")
            else:
                refusal = self._incoming_refusal()  # things may have changed while they keyed it in
        if refusal is not None:
            reason, speech = refusal
            _logger.info("autopatch %s refused: %s", who, reason)
            self._audit("phone", "Autopatch refused", f"{who}: {reason}")
            await self._say(line, speech)
            return False
        return True

    async def _run_incoming(self, line: PhoneLine, caller: str, channel: str) -> None:
        config = self._service.config
        now = self._clock()
        call = CallRecord(number=caller, actor="phone", started_at=now, state="connected", connected_at=now, direction="incoming")
        self.call = call
        self._hangup_reason = None
        self._task = asyncio.current_task()
        patch = PatchAudio(self.rate)
        self._service.begin_patch()
        self._set_patch(patch)
        speech = "Autopatch ended."
        sender: Optional[asyncio.Task] = None
        self._audit(call.actor, "Autopatch call", call.who)
        try:
            await asyncio.gather(self._say(line, "You're on the air."), self._announce(patch, "Incoming phone call.", config))
            line.connect(patch)
            sender = self._start_digits(channel, None)
            call.result = await self._talk(line, config.autopatch_max_call_seconds)
            if call.result == "time limit reached":
                speech = "Time limit reached. Autopatch ended."
        except asyncio.CancelledError:
            call.result = self._hangup_reason or "hung up"
        except (OSError, ConnectionError) as error:
            _logger.error("autopatch %s failed: %r", call.who, error)
            call.result = "the connection failed"
        finally:
            await self._stop_digits(sender)
            self._finish(call, speech)

    # -- AudioSocket server --------------------------------------------------

    async def _accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            frame = await asyncio.wait_for(read_frame_async(reader), UUID_TIMEOUT)
            call_id = str(uuid.UUID(bytes=frame.payload)) if frame.type == FrameType.UUID else None
        except (OSError, ValueError, asyncio.TimeoutError, asyncio.IncompleteReadError):
            call_id = None
        waiting = self._waiting.get(call_id) if call_id else None
        if waiting is not None and not waiting.done():
            done: "asyncio.Future[None]" = asyncio.get_running_loop().create_future()
            waiting.set_result((reader, writer, done))
            await done  # the call owns the connection until it ends
            return
        found = await self._find_incoming(call_id) if call_id else None
        if found is None:
            _logger.warning("AudioSocket connection for unknown call %s; hanging up", call_id)
            writer.write(encode_frame(FrameType.HANGUP))
            writer.close()
            return
        await self._answer(reader, writer, *found)

    def _config_changed(self, config: RepeaterConfig) -> None:
        if not config.autopatch_enabled:
            self.hangup("autopatch was turned off")
        elif not config.transmitter_enabled:
            self.hangup("the transmitter was turned off")
        elif not config.autopatch_incoming_enabled and self.call is not None and self.call.direction == "incoming":
            self.hangup("calls in were turned off")

    def _audit(self, actor: str, action: str, detail: str) -> None:
        if self._service.audit_hook is not None:
            self._service.audit_hook(actor, action, detail)
