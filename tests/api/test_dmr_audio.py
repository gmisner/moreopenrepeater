import asyncio

import numpy as np

from api import dmr_audio as dmr_audio_mod
from api.dmr_audio import DmrAudio, DmrUsrpSettings, dmr_usrp_settings_from_env
from api.service import RepeaterService
from controller.state_machine import IDLE, RECEIVING, RepeaterConfig
from link.usrp import FRAME_BYTES, FRAME_SAMPLES, HEADER, TLV_TAG_SET_INFO, TYPE_TEXT, decode, encode_voice


class FakeAudio:
    def __init__(self):
        self.dmrs = []

    def set_dmr(self, link):
        self.dmrs.append(link)

    def set_link(self, link):
        pass

    def set_patch(self, patch):
        pass

    def set_ptt(self, active):
        pass

    def set_repeating(self, repeating):
        pass

    def arm_parrot(self):
        return False

    def play(self, clip):
        pass


class FakePeer(asyncio.DatagramProtocol):
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
    peer = FakePeer()
    peer.transport, _ = await loop.create_datagram_endpoint(lambda: peer, local_addr=("127.0.0.1", 0))
    peer_port = peer.transport.get_extra_info("sockname")[1]
    service = RepeaterService(config=RepeaterConfig())
    audio = FakeAudio()
    service.audio_output = audio
    link = DmrAudio(service, DmrUsrpSettings("127.0.0.1", 0, "127.0.0.1", peer_port))
    await link.start()
    return service, audio, link, peer


def voice(seq, value=1000):
    return encode_voice(seq, np.full(FRAME_SAMPLES, value, dtype="<i2").tobytes())


def unkey(seq):
    return HEADER.pack(b"USRP", seq, 0, 0, 0, 0, 0, 0)


def test_settings_come_from_the_environment():
    assert dmr_usrp_settings_from_env({}) is None
    assert dmr_usrp_settings_from_env({"MOREOPENREPEATER_DMR_USRP_PEER": "127.0.0.1:34012"}) == DmrUsrpSettings(
        "127.0.0.1", 34011, "127.0.0.1", 34012
    )


def test_repeated_audio_goes_to_analog_bridge():
    async def scenario():
        service, audio, link, peer = await start()
        shared = audio.dmrs[0]
        for _ in range(3):
            shared.exchange(np.full(link.rate // 50, 0.25, dtype=np.float32), carrier=True)
        packets = [await asyncio.wait_for(peer.received.get(), 2) for _ in range(2)]
        assert [p.seq for p in packets] == [1, 2]
        assert all(len(p.audio) == FRAME_BYTES for p in packets)
        await link.stop()
        peer.transport.close()

    asyncio.run(scenario())


def test_analog_bridge_keying_up_is_transmitted():
    async def scenario():
        service, audio, link, peer = await start()
        for seq in range(1, 6):
            peer.send(voice(seq), link.listen_port)
        await wait_until(lambda: service.controller.state == RECEIVING)
        assert service.ptt_active and link.keyed
        peer.send(unkey(6), link.listen_port)
        await wait_until(lambda: not link.keyed)
        assert service.controller.state != RECEIVING
        await link.stop()
        peer.transport.close()

    asyncio.run(scenario())


def test_packets_from_elsewhere_are_ignored():
    async def scenario():
        service, audio, link, peer = await start()
        link.datagram_received(voice(1), ("192.0.2.1", 34012))
        await asyncio.sleep(0.05)
        assert not link.keyed and service.controller.state == IDLE
        await link.stop()
        peer.transport.close()

    asyncio.run(scenario())


def test_lost_unkey_times_out(monkeypatch):
    monkeypatch.setattr(dmr_audio_mod, "UNKEY_TIMEOUT", 0.1)

    async def scenario():
        service, audio, link, peer = await start()
        peer.send(voice(1), link.listen_port)
        await wait_until(lambda: link.keyed)
        await wait_until(lambda: not link.keyed)
        await link.stop()
        peer.transport.close()

    asyncio.run(scenario())


def _set_info(seq: int, radio_id: int, talkgroup: int, slot: int, callsign: str) -> bytes:
    call = callsign.encode("ascii") + b"\x00"
    body = bytearray(14 + len(call))
    body[0] = TLV_TAG_SET_INFO
    body[1] = len(body) - 2
    body[2] = (radio_id >> 16) & 0xFF
    body[3] = (radio_id >> 8) & 0xFF
    body[4] = radio_id & 0xFF
    body[9] = (talkgroup >> 16) & 0xFF
    body[10] = (talkgroup >> 8) & 0xFF
    body[11] = talkgroup & 0xFF
    body[12] = slot
    body[13] = 1
    body[14:] = call
    return HEADER.pack(b"USRP", seq, 0, 0, 0, TYPE_TEXT, 0, 0) + bytes(body)


def test_set_info_updates_last_heard():
    async def scenario():
        service, audio, link, peer = await start()
        assert link.status()["last_heard"] is None
        peer.send(_set_info(1, 3_120_001, 3100, 2, "W1AW"), link.listen_port)
        await wait_until(lambda: link.status()["last_heard"] is not None)
        heard = link.status()["last_heard"]
        assert heard["callsign"] == "W1AW"
        assert heard["radio_id"] == 3_120_001
        assert heard["talkgroup"] == 3100 and heard["slot"] == 2
        assert heard["ended_at"] is None and heard["active"] is False
        peer.send(voice(2), link.listen_port)
        await wait_until(lambda: link.keyed)
        assert link.status()["last_heard"]["active"] is True
        peer.send(unkey(3), link.listen_port)
        await wait_until(lambda: not link.keyed)
        done = link.status()["last_heard"]
        assert done["callsign"] == "W1AW" and done["ended_at"] is not None and done["active"] is False
        peer.send(_set_info(4, 99, 91, 1, "K1ABC"), link.listen_port)
        await wait_until(lambda: link.status()["last_heard"]["callsign"] == "K1ABC")
        assert link.status()["last_heard"]["talkgroup"] == 91
        await link.stop()
        peer.transport.close()

    asyncio.run(scenario())
