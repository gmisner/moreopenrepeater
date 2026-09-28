import asyncio
import uuid

import numpy as np

from api.autopatch import Autopatch, PatchSettings, patch_settings_from_env
from api.service import RepeaterService
from controller.state_machine import IDLE, PATCH, RepeaterConfig
from link.audiosocket import FrameType, encode_frame, read_frame_async
from playout.renderer import TTS_PREFIX

SETTINGS = PatchSettings("asterisk", 5038, "admin", "secret", "127.0.0.1", 0, "127.0.0.1:9092")
CHANNEL = "PJSIP/trunk-00000001"


class FakeAMI:
    """Plays Asterisk's side of AMI: `on_originate(action)` decides what happens to the call."""

    def __init__(self, on_originate):
        self.on_originate = on_originate
        self.actions = []
        self.closed = False
        self._events = asyncio.Queue()

    async def connect(self):
        pass

    async def send_action(self, fields):
        self.actions.append(fields)
        if fields["Action"] == "Originate":
            asyncio.get_running_loop().call_soon(self.on_originate, fields)
        return {"Response": "Success", "ActionID": fields.get("ActionID", "")}

    async def events(self):
        while (event := await self._events.get()) is not None:
            yield event

    def push(self, **event):
        self._events.put_nowait(event)

    async def close(self):
        self.closed = True
        self._events.put_nowait(None)


class FakeAudio:
    def __init__(self):
        self.patches = []
        self.played = []

    def set_patch(self, patch):
        self.patches.append(patch)

    def set_ptt(self, active): pass
    def set_repeating(self, repeating): pass
    def arm_parrot(self): return False

    def play(self, clip):
        self.played.append(clip)


class FakePhone:
    """Asterisk's AudioSocket side of an answered call."""

    def __init__(self, port, call_id):
        self.port = port
        self.call_id = call_id
        self.received = []
        self.got_hangup = asyncio.Event()

    async def connect(self):
        self.reader, self.writer = await asyncio.open_connection("127.0.0.1", self.port)
        self.writer.write(encode_frame(FrameType.UUID, uuid.UUID(self.call_id).bytes))
        self.reader_task = asyncio.create_task(self._read())

    async def _read(self):
        try:
            while True:
                frame = await read_frame_async(self.reader)
                if frame.type == FrameType.HANGUP:
                    self.got_hangup.set()
                    return
                self.received.append(np.frombuffer(frame.payload, "<i2"))
        except asyncio.IncompleteReadError:
            pass

    def say(self, level, frames=10):
        pcm = np.full(160, int(level * 32767), dtype="<i2").tobytes()
        for _ in range(frames):
            self.writer.write(encode_frame(FrameType.AUDIO, pcm))

    def hang_up(self):
        self.writer.write(encode_frame(FrameType.HANGUP))
        self.writer.close()


def make(on_originate=lambda action: None, **config):
    config = RepeaterConfig(autopatch_enabled=True, **config)
    service = RepeaterService(config=config)
    audio = FakeAudio()
    service.audio_output = audio
    ami = FakeAMI(on_originate)
    patch = Autopatch(service, SETTINGS, ami_factory=lambda *args: ami)
    return service, patch, ami, audio


async def wait_until(condition, timeout=3.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not condition():
        assert loop.time() < deadline, "timed out"
        await asyncio.sleep(0.01)


def spoken(service):
    return [c.removeprefix(TTS_PREFIX) for c in service.controller.queued_announcements]


def answer(ami, patch, phones):
    def on_originate(action):
        call_id = action["ChannelId"]
        ami.push(Event="Newchannel", Uniqueid=call_id, Channel=CHANNEL)
        ami.push(Event="OriginateResponse", ActionID=call_id, Response="Success", Reason="4", Uniqueid=call_id)
        phone = FakePhone(patch.listen_port, call_id)
        phones.append(phone)
        asyncio.ensure_future(phone.connect())
    return on_originate


def test_settings_come_from_the_ami_environment():
    assert patch_settings_from_env({}) is None
    settings = patch_settings_from_env({"MOREOPENREPEATER_AMI_HOST": "127.0.0.1", "MOREOPENREPEATER_AMI_SECRET": "s"})
    assert (settings.listen_host, settings.listen_port, settings.address) == ("127.0.0.1", 9092, "127.0.0.1:9092")


def test_answered_call_bridges_audio_until_the_far_end_hangs_up():
    async def scenario():
        service, patch, ami, audio = make()
        phones = []
        ami.on_originate = answer(ami, patch, phones)
        await patch.start()
        assert patch.dial("8605551234", "DTMF") is None
        assert service.controller.state == PATCH
        await wait_until(lambda: patch.call and patch.call.state == "connected")

        originate = ami.actions[0]
        assert originate["Channel"] == "PJSIP/8605551234@trunk"
        assert originate["Application"] == "AudioSocket"
        assert originate["Data"] == f"{originate['ChannelId']},127.0.0.1:9092"
        assert originate["Async"] == "true"

        phone = phones[0]
        bridge = audio.patches[0]
        phone.say(0.5)
        await asyncio.sleep(0.1)
        radio = np.full(320, 0.25, dtype=np.float32)
        heard = np.concatenate([bridge.exchange(radio, carrier=True) for _ in range(8)])
        assert heard.max() > 0.3  # the phone's audio, to transmit
        await wait_until(lambda: any(frame.max() > 5000 for frame in phone.received))  # the radio's audio

        phone.hang_up()
        await wait_until(lambda: patch.call is None)
        assert patch.last_call.result == "the other party hung up"
        assert audio.patches[-1] is None
        assert service.controller.state != PATCH
        assert spoken(service) == ["Autopatch ended."]
        assert ami.closed
        await patch.stop()

    asyncio.run(scenario())


def test_hangup_code_ends_a_connected_call():
    async def scenario():
        service, patch, ami, _ = make()
        phones = []
        ami.on_originate = answer(ami, patch, phones)
        await patch.start()
        patch.dial("911", "DTMF")
        await wait_until(lambda: patch.call and patch.call.state == "connected")
        service.simulate_dtmf("#")
        await asyncio.wait_for(phones[0].got_hangup.wait(), 2)
        await wait_until(lambda: patch.call is None)
        assert patch.last_call.result == "hung up by a user"
        await patch.stop()

    asyncio.run(scenario())


def test_busy_line():
    async def scenario():
        service, patch, ami, _ = make()
        ami.on_originate = lambda a: ami.push(
            Event="OriginateResponse", ActionID=a["ActionID"], Response="Failure", Reason="5"
        )
        await patch.start()
        patch.dial("8605551234", "DTMF")
        await wait_until(lambda: patch.call is None)
        assert patch.last_call.result == "busy"
        assert spoken(service) == ["The line is busy."]
        assert service.controller.state != PATCH
        await patch.stop()

    asyncio.run(scenario())


def test_cancelling_while_ringing_hangs_up_the_channel():
    async def scenario():
        service, patch, ami, _ = make()
        ami.on_originate = lambda a: ami.push(Event="Newchannel", Uniqueid=a["ChannelId"], Channel=CHANNEL)
        await patch.start()
        patch.dial("8605551234", "DTMF")
        await wait_until(lambda: len(ami.actions) == 1)
        await asyncio.sleep(0.05)
        service.simulate_dtmf("#")
        await wait_until(lambda: patch.call is None)
        assert ami.actions[-1] == {"Action": "Hangup", "Channel": CHANNEL}
        assert patch.last_call.result == "hung up by a user"
        assert spoken(service) == ["Autopatch cancelled."]
        await patch.stop()

    asyncio.run(scenario())


def test_time_limit_ends_the_call():
    async def scenario():
        service, patch, ami, _ = make(autopatch_max_call_seconds=0.3)
        phones = []
        ami.on_originate = answer(ami, patch, phones)
        await patch.start()
        patch.dial("911", "DTMF")
        await wait_until(lambda: patch.call is None)
        assert patch.last_call.result == "time limit reached"
        await asyncio.wait_for(phones[0].got_hangup.wait(), 2)
        assert spoken(service) == ["Time limit reached. Autopatch ended."]
        await patch.stop()

    asyncio.run(scenario())


def test_refused_numbers_never_reach_asterisk():
    async def scenario():
        service, patch, ami, _ = make()
        await patch.start()
        assert patch.dial("19005551234", "DTMF") == "That number is not allowed."
        assert patch.dial("8605551234", "admin") is None
        assert patch.dial("911", "DTMF") == "A call is already in progress."
        assert spoken(service) == ["That number is not allowed.", "A call is already in progress."]
        patch.hangup("test over")
        await patch.stop()
        assert [a["Action"] for a in ami.actions] == ["Originate"]

    asyncio.run(scenario())


def test_no_calls_while_autopatch_or_the_transmitter_is_off():
    async def scenario():
        service, patch, _, _ = make(transmitter_enabled=False)
        await patch.start()
        assert patch.dial("911", "admin") == "The transmitter is off."
        service.update_config(transmitter_enabled=True, autopatch_enabled=False)
        assert patch.dial("911", "admin") == "Autopatch is turned off."
        await patch.stop()

    asyncio.run(scenario())


def test_turning_the_transmitter_off_hangs_up():
    async def scenario():
        service, patch, ami, _ = make()
        phones = []
        ami.on_originate = answer(ami, patch, phones)
        await patch.start()
        patch.dial("911", "admin")
        await wait_until(lambda: patch.call and patch.call.state == "connected")
        service.update_config(transmitter_enabled=False)
        await wait_until(lambda: patch.call is None)
        assert patch.last_call.result == "the transmitter was turned off"
        assert service.controller.state == IDLE
        await patch.stop()

    asyncio.run(scenario())


def test_unknown_audiosocket_connections_are_hung_up():
    async def scenario():
        _, patch, _, _ = make()
        await patch.start()
        phone = FakePhone(patch.listen_port, str(uuid.uuid4()))
        await phone.connect()
        await asyncio.wait_for(phone.got_hangup.wait(), 2)
        await patch.stop()

    asyncio.run(scenario())


def test_dial_without_asterisk_configured():
    async def scenario():
        service = RepeaterService(config=RepeaterConfig(autopatch_enabled=True))
        patch = Autopatch(service, None)
        await patch.start()
        assert patch.dial("911", "DTMF") == "Autopatch is not available."
        assert patch.status()["available"] is False

    asyncio.run(scenario())
