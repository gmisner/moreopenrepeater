import asyncio
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.allstar_audio import AllStarAudio, UsrpSettings
from api.allstar_node import AllStarNode, AllStarSetupError
from api.app import create_app
from api.assets import AudioAssetStore
from api.autopatch import PatchSettings
from api.service import LINK_AUDIO_NODE, RepeaterService
from controller.events import RemoteKeyed
from controller.state_machine import IDLE, RECEIVING, RepeaterConfig
from link.asterisk_files import AsteriskFiles

AMI = PatchSettings("127.0.0.1", 5038, "admin", "secret", "127.0.0.1", 0, "127.0.0.1:9092")

RPT = """\
[node-main](!)
rxchannel = Local/pseudo            ; No radio (hub)
duplex = 2

#tryinclude "custom/rpt/*.conf"

[1999](node-main)
rxchannel = SimpleUSB/1999      ; SimpleUSB

[1998](node-main)
"""

MODULES = """\
[modules]
autoload = no
load = chan_simpleusb.so              ; SimpleUSB Radio Interface Channel Driver
noload = chan_usbradio.so               ; USB Console Channel Driver
noload = chan_usrp.so                   ; USRP Channel Module
"""


class FakeAMI:
    """Asterisk going down as soon as it's told to restart."""

    def __init__(self):
        self.commands = []

    def client(self, *args):
        return self

    async def connect(self): pass

    async def send_action(self, fields):
        self.commands.append(fields["Command"])
        raise ConnectionError("AMI connection closed before a response arrived")

    async def close(self): pass


class FakeAudio:
    def __init__(self):
        self.links = []

    def set_link(self, link): self.links.append(link)
    def set_patch(self, patch): pass
    def set_ptt(self, active): pass
    def set_repeating(self, repeating): pass
    def arm_parrot(self): return False
    def play(self, clip): pass


def make(tmp_path, rpt=RPT, ami=AMI, manual=None):
    etc = tmp_path / "etc"
    etc.mkdir()
    (etc / "rpt.conf").write_text(rpt)
    (etc / "modules.conf").write_text(MODULES)
    service = RepeaterService(config=RepeaterConfig())
    service.audio_output = FakeAudio()
    fake = FakeAMI()
    node = AllStarNode(
        AsteriskFiles(str(etc), backup_dir=tmp_path / "backups"),
        ami,
        AllStarAudio(service, None),
        listen=("127.0.0.1", 0),
        manual=manual,
        ami_factory=fake.client,
    )
    return node, etc, fake, service


def test_lists_the_nodes(tmp_path):
    node, *_ = make(tmp_path)
    status = asyncio.run(node.status())
    assert status["available"] and status["node"] is None
    assert [(n["number"], n["rxchannel"]) for n in status["nodes"]] == [("1999", "SimpleUSB/1999"), ("1998", "Local/pseudo")]


def test_using_a_node_rewrites_its_radio_restarts_asterisk_and_starts_the_audio(tmp_path):
    async def scenario():
        node, etc, fake, service = make(tmp_path)
        status = await node.use("1999")
        rpt, modules = (etc / "rpt.conf").read_text(), (etc / "modules.conf").read_text()
        assert "rxchannel = USRP/127.0.0.1:0:32001  ; set by moreopenrepeater" in rpt
        assert "load = chan_usrp.so  ; set by moreopenrepeater" in modules
        assert "noload = chan_simpleusb.so  ; set by moreopenrepeater" in modules  # nothing else uses it
        assert fake.commands == ["core restart now"]
        assert status["node"] == "1999" and not status["restart_needed"]
        assert status["audio"]["running"] and status["audio"]["node"] == "127.0.0.1:32001"
        assert service.link_audio
        assert sorted(p.name.split(".")[0] for p in (tmp_path / "backups").iterdir()) == ["modules", "rpt"]

        status = await node.release()
        assert (etc / "rpt.conf").read_text() == RPT
        assert (etc / "modules.conf").read_text() == MODULES
        assert fake.commands == ["core restart now"] * 2
        assert status["node"] is None and not status["audio"]["running"] and not service.link_audio

    asyncio.run(scenario())


def test_one_node_at_a_time(tmp_path):
    async def scenario():
        node, etc, *_ = make(tmp_path)
        await node.use("1999")
        status = await node.use("1998")
        assert [n["number"] for n in status["nodes"] if n["controlled"]] == ["1998"]
        assert "SimpleUSB/1999      ; SimpleUSB" in (etc / "rpt.conf").read_text().split("[1998]")[0]
        await node.stop()

    asyncio.run(scenario())


def test_another_usrp_node_keeps_its_port(tmp_path):
    rpt = RPT.replace("[1998](node-main)\n", "[1998](node-main)\nrxchannel = USRP/127.0.0.1:34002:32001\n")
    async def scenario():
        node, etc, *_ = make(tmp_path, rpt=rpt)
        status = await node.use("1999")
        assert status["audio"]["node"] == "127.0.0.1:32002"
        await node.stop()

    asyncio.run(scenario())


def test_startup_finds_the_node_it_set_up(tmp_path):
    async def scenario():
        node, etc, *_ = make(tmp_path)
        await node.use("1999")
        await node.stop()
        (tmp_path / "again").mkdir()
        again, *_ = make(tmp_path / "again")
        again._files = node._files
        await again.start()
        assert again.audio.status()["node"] == "127.0.0.1:32001"
        await again.stop()

    asyncio.run(scenario())


def test_without_ami_it_asks_for_a_restart(tmp_path):
    async def scenario():
        node, *_ = make(tmp_path, ami=None)
        status = await node.use("1999")
        assert status["restart_needed"] and not status["can_restart"]
        await node.stop()

    asyncio.run(scenario())


def test_a_node_set_up_by_hand_is_left_alone(tmp_path):
    async def scenario():
        node, etc, *_ = make(tmp_path, manual=UsrpSettings("127.0.0.1", 0, "127.0.0.1", 32001))
        await node.start()
        status = await node.status()
        assert status["manual"] and not status["available"] and status["audio"]["running"]
        with pytest.raises(AllStarSetupError):
            await node.use("1999")
        assert (etc / "rpt.conf").read_text() == RPT
        await node.stop()

    asyncio.run(scenario())


def test_unknown_node(tmp_path):
    node, *_ = make(tmp_path)
    with pytest.raises(AllStarSetupError, match="no node 2000"):
        asyncio.run(node.use("2000"))


def test_link_keyups_from_ami_dont_key_the_repeater_while_the_node_sends_audio():
    service = RepeaterService(config=RepeaterConfig())
    service.link_audio = True
    service.handle_link_event(RemoteKeyed(node_id="1998", keyed=True))
    assert service.controller.state == IDLE
    service.handle_link_event(RemoteKeyed(node_id=LINK_AUDIO_NODE, keyed=True))
    assert service.controller.state == RECEIVING


def test_routes(tmp_path):
    node, etc, fake, service = make(tmp_path)
    app = create_app(
        service=service,
        start_background_tick=False,
        assets_store=AudioAssetStore(Path(tempfile.mkdtemp()) / "audio"),
        log_path=tmp_path / "test.log",
        allstar_node=node,
    )
    with TestClient(app) as client:
        assert client.get("/api/allstar").json()["nodes"][0]["number"] == "1999"
        assert client.put("/api/allstar", json={"node": "../x"}).status_code == 422
        body = client.put("/api/allstar", json={"node": "1999"}).json()
        assert body["node"] == "1999"
        assert client.delete("/api/allstar").json()["node"] is None
    assert (etc / "rpt.conf").read_text() == RPT
