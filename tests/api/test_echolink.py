import asyncio
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.allstar_audio import AllStarAudio
from api.allstar_node import AllStarNode, AllStarSetupError
from api.app import create_app
from api.assets import AudioAssetStore
from api.autopatch import PatchSettings
from api.echolink import EchoLink, EchoLinkSettings
from api.service import RepeaterService
from controller.state_machine import RepeaterConfig
from link.asterisk_files import AsteriskFiles

AMI = PatchSettings("127.0.0.1", 5038, "admin", "secret", "127.0.0.1", 0, "127.0.0.1:9092")

RPT = """\
[node-main](!)
rxchannel = Local/pseudo

[1999](node-main)
rxchannel = SimpleUSB/1999

[1998](node-main)
"""

MODULES = """\
[modules]
autoload = no
noload = chan_echolink.so               ; Echolink Channel Driver
noload = chan_usrp.so                   ; USRP Channel Module
"""

ECHOLINK = """\
[el0]
call = INVALID						; Change this!
pwd = INVALID						; Change this!
name = YOUR NAME					; Change this!
qth = INVALID						; Change this!
email = INVALID						; Change this!
node = 000000                       ; Change this!
lat = 0.0							; Latitude in decimal degrees
maxstns = 20						; Max Stations
astnode = 1999						; Change this!
context = radio-secure				; Default in code is radio-secure
server1 = nasouth.echolink.org

#tryinclude "custom/echolink.conf"
"""

STATION = EchoLinkSettings(
    callsign="W1AW-R", name="Hiram", location="Newington, CT", email="w1aw@example.org",
    node_number="123456", password="s3cret", lat=41.714, lon=-72.727, frequency_mhz=146.94, tone_hz=100.0,
)


class FakeAMI:
    def __init__(self):
        self.actions = []
        self.loaded = set()

    def client(self, *args):
        return self

    async def connect(self): pass

    async def send_action(self, fields):
        self.actions.append(fields.get("Command") or fields["Action"])
        if fields["Action"] == "ModuleCheck":
            return {"Response": "Success" if fields["Module"] in self.loaded else "Error"}
        raise ConnectionError("closed")  # restarting

    async def close(self): pass


def make(tmp_path):
    etc = tmp_path / "etc"
    etc.mkdir()
    for name, text in (("rpt.conf", RPT), ("modules.conf", MODULES), ("echolink.conf", ECHOLINK)):
        (etc / name).write_text(text)
    service = RepeaterService(config=RepeaterConfig())
    fake = FakeAMI()
    fake.service = service
    node = AllStarNode(AsteriskFiles(str(etc)), AMI, AllStarAudio(service, None), listen=("127.0.0.1", 0), ami_factory=fake.client)
    return node, EchoLink(node), etc, fake


def test_asl3_placeholders_read_as_blank(tmp_path):
    _, echolink, *_ = make(tmp_path)
    status = asyncio.run(echolink.status())
    assert status["available"] and not status["enabled"]
    s = status["settings"]
    assert (s["callsign"], s["name"], s["node_number"], s["has_password"], s["set_here"]) == ("", "", "", False, False)


def test_needs_the_repeaters_node_first(tmp_path):
    _, echolink, *_ = make(tmp_path)
    with pytest.raises(AllStarSetupError, match="node first"):
        asyncio.run(echolink.save(STATION))


def test_saving_writes_el0_loads_the_driver_and_restarts(tmp_path):
    async def scenario():
        node, echolink, etc, fake = make(tmp_path)
        node.node = "1998"
        fake.loaded.add("chan_echolink.so")
        status = await echolink.save(STATION)
        text = (etc / "echolink.conf").read_text()
        assert "call = W1AW-R  ; set by moreopenrepeater\npwd = s3cret  ; set by moreopenrepeater\n" in text
        assert "astnode = 1998  ; set by moreopenrepeater" in text
        assert ";moreopenrepeater was: astnode = 1999" in text
        assert text.endswith('context = radio-secure\t\t\t\t; Default in code is radio-secure\nserver1 = nasouth.echolink.org\n\n#tryinclude "custom/echolink.conf"\n')
        assert "load = chan_echolink.so  ; set by moreopenrepeater" in (etc / "modules.conf").read_text()
        assert "core restart now" in fake.actions
        assert status["enabled"] and status["loaded"]
        s = status["settings"]
        assert (s["callsign"], s["node_number"], s["has_password"], s["frequency_mhz"], s["set_here"]) == ("W1AW-R", "123456", True, 146.94, True)
        assert "s3cret" not in str(status)

        again = EchoLinkSettings(**{**STATION.__dict__, "password": None, "name": "Hiram Percy Maxim"})
        await echolink.save(again)
        text = (etc / "echolink.conf").read_text()
        assert "pwd = s3cret  ; set by moreopenrepeater" in text and "name = Hiram Percy Maxim" in text
        assert text.count("; set by moreopenrepeater") == 12

        status = await echolink.disable()
        assert (etc / "modules.conf").read_text() == MODULES
        assert not status["enabled"] and status["settings"]["callsign"] == "W1AW-R"

    asyncio.run(scenario())


def test_a_password_is_required_the_first_time(tmp_path):
    node, echolink, *_ = make(tmp_path)
    node.node = "1999"
    with pytest.raises(AllStarSetupError, match="password"):
        asyncio.run(echolink.save(EchoLinkSettings(**{**STATION.__dict__, "password": None})))


def test_echolink_follows_the_repeater_to_another_node(tmp_path):
    async def scenario():
        node, echolink, etc, _ = make(tmp_path)
        await node.use("1999")
        await echolink.save(STATION)
        await node.use("1998")
        assert "astnode = 1998  ; set by moreopenrepeater" in (etc / "echolink.conf").read_text()
        await node.stop()

    asyncio.run(scenario())


def test_routes(tmp_path):
    node, _, etc, fake = make(tmp_path)
    app = create_app(
        service=fake.service,
        start_background_tick=False,
        assets_store=AudioAssetStore(Path(tempfile.mkdtemp()) / "audio"),
        log_path=tmp_path / "test.log",
        allstar_node=node,
    )
    body = {**STATION.__dict__}
    with TestClient(app) as client:
        assert client.get("/api/allstar/echolink").json()["available"]
        assert client.put("/api/allstar/echolink", json={**body, "callsign": "W1AW-X"}).status_code == 422
        assert client.put("/api/allstar/echolink", json={**body, "name": "x ; y"}).status_code == 422
        assert client.put("/api/allstar/echolink", json={**body, "password": "a b"}).status_code == 422
        assert client.put("/api/allstar/echolink", json=body).status_code == 502  # no node chosen yet
        client.put("/api/allstar", json={"node": "1999"})
        response = client.put("/api/allstar/echolink", json=body)
        assert response.status_code == 200 and "s3cret" not in response.text
        assert "s3cret" not in (tmp_path / "test.log").read_text()
        assert client.delete("/api/allstar/echolink").json()["enabled"] is False
    assert "\nnoload = chan_echolink.so               ; Echolink Channel Driver\n" in (etc / "modules.conf").read_text()
