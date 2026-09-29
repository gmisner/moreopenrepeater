import asyncio

import numpy as np

from api import allstar_audio
from api.allstar_audio import AllStarAudio, UsrpSettings, usrp_settings_from_env
from api.service import RepeaterService
from controller.state_machine import IDLE, RECEIVING, RepeaterConfig
from link.usrp import FRAME_BYTES, FRAME_SAMPLES, HEADER, decode, encode_voice


class FakeAudio:
    def __init__(self):
        self.links = []

    def set_link(self, link):
        self.links.append(link)

    def set_patch(self, patch): pass
    def set_ptt(self, active): pass
    def set_repeating(self, repeating): pass
    def arm_parrot(self): return False
    def play(self, clip): pass


class FakeNode(asyncio.DatagramProtocol):
    """app_rpt's chan_usrp end."""

    def __init__(self):
        self.received = asyncio.Queue()

    def datagram_received(self, data, addr):
        self.received.put_nowait(decode(data))

    def send(self, data, port):
        self.transport.sendto(data, ("127.0.0.1", port))


async def wait_until(condition, timeout=3.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not condition():
        assert loop.time() < deadline, "timed out"
        await asyncio.sleep(0.01)


async def start():
    loop = asyncio.get_running_loop()
    node = FakeNode()
    node.transport, _ = await loop.create_datagram_endpoint(lambda: node, local_addr=("127.0.0.1", 0))
    node_port = node.transport.get_extra_info("sockname")[1]
    service = RepeaterService(config=RepeaterConfig())
    audio = FakeAudio()
    service.audio_output = audio
    link = AllStarAudio(service, UsrpSettings("127.0.0.1", 0, "127.0.0.1", node_port))
    await link.start()
    return service, audio, link, node


def voice(seq, value=1000):
    return encode_voice(seq, np.full(FRAME_SAMPLES, value, dtype="<i2").tobytes())


def unkey(seq):
    return HEADER.pack(b"USRP", seq, 0, 0, 0, 0, 0, 0)


def test_settings_come_from_the_environment():
    assert usrp_settings_from_env({}) is None
    assert usrp_settings_from_env({"MOREOPENREPEATER_USRP_NODE": "127.0.0.1:32001"}) == UsrpSettings("127.0.0.1", 34001, "127.0.0.1", 32001)
    settings = usrp_settings_from_env({"MOREOPENREPEATER_USRP_NODE": "asl:32001", "MOREOPENREPEATER_USRP_LISTEN": "0.0.0.0:34002"})
    assert settings == UsrpSettings("0.0.0.0", 34002, "asl", 32001)


def test_repeated_audio_goes_to_the_node_in_20_ms_frames():
    async def scenario():
        service, audio, link, node = await start()
        shared = audio.links[0]
        assert link.status()["running"]
        for _ in range(3):
            shared.exchange(np.full(link.rate // 50, 0.25, dtype=np.float32), carrier=True)
        packets = [await asyncio.wait_for(node.received.get(), 2) for _ in range(2)]
        assert [p.seq for p in packets] == [1, 2]
        assert all(len(p.audio) == FRAME_BYTES for p in packets)
        samples = np.frombuffer(packets[1].audio, dtype="<i2")
        assert abs(int(samples[-1]) - 8191) < 200
        await link.stop()
        assert audio.links[-1] is None
        node.transport.close()

    asyncio.run(scenario())


def test_the_node_keying_up_is_transmitted_until_it_unkeys():
    async def scenario():
        service, audio, link, node = await start()
        shared = audio.links[0]
        for seq in range(1, 6):
            node.send(voice(seq), link.listen_port)
        await wait_until(lambda: service.controller.state == RECEIVING)
        assert service.ptt_active and link.status()["keyed"]
        await wait_until(lambda: shared._queued > 0)
        node.send(unkey(6), link.listen_port)
        await wait_until(lambda: not link.keyed)
        assert service.controller.state != RECEIVING
        await link.stop()
        node.transport.close()

    asyncio.run(scenario())


def test_a_lost_unkey_times_out(monkeypatch):
    monkeypatch.setattr(allstar_audio, "UNKEY_TIMEOUT", 0.1)

    async def scenario():
        service, audio, link, node = await start()
        node.send(voice(1), link.listen_port)
        await wait_until(lambda: link.keyed)
        await wait_until(lambda: not link.keyed)
        assert service.controller.state != RECEIVING
        await link.stop()
        node.transport.close()

    asyncio.run(scenario())


def test_packets_from_anywhere_but_the_node_are_ignored():
    async def scenario():
        service, audio, link, node = await start()
        link.datagram_received(voice(1), ("192.0.2.1", 32001))
        await asyncio.sleep(0.05)
        assert not link.keyed and service.controller.state == IDLE
        await link.stop()
        node.transport.close()

    asyncio.run(scenario())


def test_stopping_while_keyed_unkeys():
    async def scenario():
        service, audio, link, node = await start()
        node.send(voice(1), link.listen_port)
        await wait_until(lambda: link.keyed)
        await link.stop()
        assert service.controller.state != RECEIVING
        node.transport.close()

    asyncio.run(scenario())


def test_unconfigured_does_nothing():
    async def scenario():
        service = RepeaterService(config=RepeaterConfig())
        audio = FakeAudio()
        service.audio_output = audio
        link = AllStarAudio(service, None)
        await link.start()
        await link.stop()
        assert audio.links == []
        assert link.status() == {"configured": False, "running": False, "error": None, "node": None, "keyed": False}

    asyncio.run(scenario())


def test_a_port_in_use_is_reported():
    async def scenario():
        service, audio, taken, node = await start()
        link = AllStarAudio(service, UsrpSettings("127.0.0.1", taken.listen_port, "127.0.0.1", 32001))
        await link.start()
        assert "couldn't set up USRP" in link.status()["error"] and not link.status()["running"]
        await taken.stop()
        node.transport.close()

    asyncio.run(scenario())
