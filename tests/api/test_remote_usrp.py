"""AllStar and DMR setup from the dashboard when the core is apart (container install): the API edits the node's and the
bridge's files and tells the core where they are."""
import asyncio
import json
import shutil
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_allstar_node import EXTENSIONS, MODULES, REG, RPT
from test_split_app import CoreThread, wait_for

from api.allstar_audio import UsrpSettings
from api.app import create_app
from api.assets import AudioAssetStore
from api.dmr import DmrBridge, DmrSettings, DmrSetupError
from api.dmr_audio import DmrUsrpSettings
from api.remote_core import RemoteCore
from api.remote_usrp import RemoteUsrpAudio
from api.service.core_handle import CommandRefused, CoreUnavailable
from core.build import build_core
from link import ini_file
from link.asterisk_files import asterisk_files_from_env


class FakeRemote:
    """What RemoteUsrpAudio needs of the core."""

    def __init__(self):
        self.sent = []
        self.statuses = {}
        self.fail = None
        self.listeners = []

    def on_connect(self, listener):
        self.listeners.append(listener)

    def hardware_status(self, name):
        if name not in self.statuses:
            raise CoreUnavailable("not known")
        return self.statuses[name]

    async def command(self, name, **params):
        if self.fail is not None:
            raise self.fail
        self.sent.append((name, params))


def test_it_tells_the_core_where_the_node_is():
    remote = FakeRemote()
    audio = RemoteUsrpAudio(remote, "allstar", "allstar_node")
    asyncio.run(audio.configure(UsrpSettings("0.0.0.0", 34001, "allstar", 32001)))
    asyncio.run(audio.configure(None))
    assert remote.sent == [
        ("usrp_configure", {"service": "allstar", "peer": {"host": "allstar", "port": 32001}}),
        ("usrp_configure", {"service": "allstar", "peer": None}),
    ]


def test_the_bridge_s_address_is_its_peer():
    remote = FakeRemote()
    asyncio.run(RemoteUsrpAudio(remote, "dmr", "dmr").configure(DmrUsrpSettings("0.0.0.0", 34011, "172.28.0.30", 34012)))
    assert remote.sent == [("usrp_configure", {"service": "dmr", "peer": {"host": "172.28.0.30", "port": 34012}})]


def test_a_core_that_is_away_does_not_fail_the_save_and_is_told_when_it_connects():
    remote = FakeRemote()
    remote.fail = CoreUnavailable("away")
    audio = RemoteUsrpAudio(remote, "allstar", "allstar_node")
    asyncio.run(audio.configure(UsrpSettings("0.0.0.0", 34001, "allstar", 32001)))  # no exception
    assert remote.sent == []

    remote.fail = None

    async def connect():
        remote.listeners[0]()
        await asyncio.sleep(0.05)

    asyncio.run(connect())
    assert remote.sent == [("usrp_configure", {"service": "allstar", "peer": {"host": "allstar", "port": 32001}})]


def test_nothing_is_sent_on_connect_before_the_dashboard_has_asked_for_anything():
    remote = FakeRemote()
    RemoteUsrpAudio(remote, "allstar", "allstar_node")

    async def connect():
        remote.listeners[0]()
        await asyncio.sleep(0.02)

    asyncio.run(connect())
    assert remote.sent == []


def test_a_refusal_is_shown_in_the_status_until_it_is_fixed():
    remote = FakeRemote()
    remote.fail = CommandRefused("command.hardware_refused", "couldn't set up USRP: no such host")
    audio = RemoteUsrpAudio(remote, "allstar", "allstar_node")
    asyncio.run(audio.configure(UsrpSettings("0.0.0.0", 34001, "nowhere", 32001)))
    assert audio.status()["error"] == "couldn't set up USRP: no such host"
    remote.fail = None
    asyncio.run(audio.configure(UsrpSettings("0.0.0.0", 34001, "allstar", 32001)))
    assert audio.status()["error"] is None


def test_the_status_is_the_cores_and_does_not_invent_one_when_it_is_away():
    remote = FakeRemote()
    audio = RemoteUsrpAudio(remote, "allstar", "allstar_node")
    assert audio.status()["running"] is False and audio.status()["configured"] is False
    remote.statuses["allstar_node"] = {"audio": {"configured": True, "running": True, "error": None, "node": "allstar:32001", "keyed": False}}
    assert audio.status()["running"] is True and audio.status()["node"] == "allstar:32001"


# -- the real thing: the dashboard's AllStar and DMR pages on an API that holds none of the radio ----------------


@pytest.fixture
def sock_dir():
    """Short path: AF_UNIX paths are limited to about 104 characters."""
    path = Path(tempfile.mkdtemp(prefix="mor-", dir="/tmp"))
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def etc(tmp_path):
    folder = tmp_path / "asterisk"
    folder.mkdir()
    (folder / "rpt.conf").write_text(RPT)
    (folder / "modules.conf").write_text(MODULES)
    (folder / "rpt_http_registrations.conf").write_text(REG)
    (folder / "extensions.conf").write_text(EXTENSIONS)
    return folder


def two_processes(tmp_path, sock_dir, monkeypatch, **env):
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "state.json").write_text(json.dumps({"config": {"callsign": "W1AW", "audio_enabled": False}}))
    core = build_core({
        "MOREOPENREPEATER_DATA_DIR": str(tmp_path / "core"), "MOREOPENREPEATER_LOG_PATH": str(tmp_path / "c.log"),
        "MOREOPENREPEATER_USRP_LISTEN": "127.0.0.1:0", "MOREOPENREPEATER_DMR_USRP_LISTEN": "127.0.0.1:0",
    })
    for name in ("MOREOPENREPEATER_AMI_HOST", "MOREOPENREPEATER_AMI_SECRET", "MOREOPENREPEATER_AUTH_USER",
                 "MOREOPENREPEATER_AUTH_PASSWORD", "MOREOPENREPEATER_ASTERISK_SHELL", "MOREOPENREPEATER_DMR_USRP_PEER"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MOREOPENREPEATER_USRP_LISTEN", "127.0.0.1:0")
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    thread = CoreThread(core, sock_dir / "core.sock")
    remote = RemoteCore(sock_dir / "core.sock")
    tmp = Path(tempfile.mkdtemp())
    app = create_app(service=remote, assets_store=AudioAssetStore(tmp / "audio"), log_path=tmp / "api.log")
    return core, thread, TestClient(app), remote


def test_the_allstar_page_sets_up_the_node_and_the_core_points_its_audio_at_it(tmp_path, sock_dir, etc, monkeypatch):
    core, thread, client, remote = two_processes(tmp_path, sock_dir, monkeypatch, MOREOPENREPEATER_ASTERISK_CONFIG_DIR=str(etc))
    with thread, client:
        wait_for(lambda: remote.connected)
        wait_for(lambda: client.get("/api/autopatch").status_code == 200)  # the core's hardware has been read once
        page = client.get("/api/allstar").json()
        assert page["available"] is True and [n["number"] for n in page["nodes"]] == ["1999", "1998"]

        saved = client.put("/api/allstar", json={"node": "1999"})
        assert saved.status_code == 200, saved.text
        assert "rxchannel = USRP/127.0.0.1:0:32001" in (etc / "rpt.conf").read_text()  # the API wrote the node's file
        wait_for(lambda: core.runtime.allstar_node.audio.status()["running"])  # and the core, not the API, runs the audio
        assert core.runtime.allstar_node.audio.status()["node"] == "127.0.0.1:32001"
        wait_for(lambda: client.get("/api/allstar").json()["audio"]["running"])  # which the page then reports
        assert json.loads((tmp_path / "core" / "usrp-endpoints.json").read_text())["allstar"]["port"] == 32001

        assert client.delete("/api/allstar").status_code == 200
        wait_for(lambda: not core.runtime.allstar_node.audio.status()["running"])


def test_the_allstar_page_waits_for_the_node_to_exist_without_a_restart(tmp_path, sock_dir, etc, monkeypatch):
    (etc / "rpt.conf").unlink()  # the node's container has not started: its folder is empty
    core, thread, client, remote = two_processes(tmp_path, sock_dir, monkeypatch, MOREOPENREPEATER_ASTERISK_CONFIG_DIR=str(etc))
    with thread, client:
        wait_for(lambda: remote.connected)
        page = client.get("/api/allstar").json()
        assert page["available"] is True and page["error"] and page["nodes"] == []
        (etc / "rpt.conf").write_text(RPT)  # and now it has
        assert [n["number"] for n in client.get("/api/allstar").json()["nodes"]] == ["1999", "1998"]


def test_a_core_that_was_away_when_the_node_was_set_up_is_pointed_at_it_when_it_comes_up(tmp_path, sock_dir, monkeypatch):
    core, thread, _client, _remote = two_processes(tmp_path, sock_dir, monkeypatch)
    remote = RemoteCore(sock_dir / "late.sock")
    thread = CoreThread(core, sock_dir / "late.sock")
    audio = RemoteUsrpAudio(remote, "allstar", "allstar_node")

    async def go():
        remote.start()
        try:
            await audio.configure(UsrpSettings("0.0.0.0", 0, "127.0.0.1", 32001))  # nobody is there: it does not raise
            assert not core.runtime.allstar_node.audio.status()["running"]
            thread.__enter__()  # the core starts, and the API's stream to it connects
            try:
                for _ in range(200):
                    if core.runtime.allstar_node.audio.status()["running"]:
                        break
                    await asyncio.sleep(0.05)
                assert core.runtime.allstar_node.audio.status()["node"] == "127.0.0.1:32001"
            finally:
                thread.__exit__()
        finally:
            await remote.stop()
            await asyncio.sleep(0.1)  # let what it cancelled finish before the loop closes

    asyncio.run(go())


def test_with_no_folder_given_the_allstar_page_is_only_what_the_core_reports(tmp_path, sock_dir, monkeypatch):
    core, thread, client, remote = two_processes(tmp_path, sock_dir, monkeypatch)
    with thread, client:
        wait_for(lambda: remote.connected)
        wait_for(lambda: client.get("/api/allstar").status_code == 200)
        assert client.get("/api/allstar").json()["available"] is False


def test_asterisk_files_in_a_container_exist_before_the_node_wrote_anything(tmp_path):
    env = {"MOREOPENREPEATER_ASTERISK_CONFIG_DIR": str(tmp_path)}
    assert asterisk_files_from_env(env, None) is None  # a host install without AllStar has nothing to edit
    assert asterisk_files_from_env(env, None, node_in_container=True) is not None
    assert asterisk_files_from_env({"MOREOPENREPEATER_ASTERISK_CONFIG_DIR": str(tmp_path / "no")}, None, node_in_container=True) is None


# -- DMR -----------------------------------------------------------------------------------------------------------

SEEDED_ANALOG = """\
[GENERAL]
useEmulator = true

[AMBE_AUDIO]
txTg = 9

[USRP]
address = 172.28.0.10
txPort = 34011
rxPort = 34012
"""
SEEDED_MMDVM = """\
[General]
Callsign=N0CALL
Id=0

[DMR Network]
Address=
Port=62031
Password=
"""
REQUEST = {
    "callsign": "W1AW", "dmr_id": "3120001", "repeater_ssid": "01", "password": "pw-secret-1", "network": "brandmeister",
    "host": "3102.repeater.net", "port": 62031, "talkgroup": 9, "slot": 2, "color_code": 1,
}


class QuietAudio:
    settings = None

    def __init__(self):
        self.configured = []

    def status(self):
        return {"configured": False, "running": False, "error": None, "keyed": False}

    async def configure(self, settings):
        self.configured.append(settings)


def bridge_in(tmp_path, **kwargs):
    audio = QuietAudio()
    tmp_path.mkdir(exist_ok=True)
    return DmrBridge(tmp_path, audio, restart=None, usrp=DmrUsrpSettings("0.0.0.0", 34011, "172.28.0.30", 34012),
                     bridge_seeds_config=True, **kwargs), audio


def test_a_bridge_that_has_not_started_is_waited_for_and_nothing_is_written_for_it(tmp_path):
    tmp_path = tmp_path / "dmr"
    bridge, _ = bridge_in(tmp_path)
    status = asyncio.run(bridge.status())
    assert status["available"] is False and "How to set this up below" in status["error"] and "docker compose" not in status["error"]
    assert list(tmp_path.iterdir()) == []  # not even the template files: the bridge writes the first ones, with its addresses
    bridge.ensure_templates()
    assert list(tmp_path.iterdir()) == []
    with pytest.raises(DmrSetupError, match="has not started"):
        asyncio.run(bridge.save(DmrSettings(**{k: v for k, v in REQUEST.items() if k in DmrSettings.__dataclass_fields__})))
    with pytest.raises(DmrSetupError):
        asyncio.run(bridge.select_talkgroup(100))
    with pytest.raises(DmrSetupError, match="starts from the repeater"):
        asyncio.run(bridge.request_install())
    assert list(tmp_path.iterdir()) == []


def test_saving_keeps_the_address_the_bridge_sends_to_and_points_the_core_at_the_bridge(tmp_path):
    tmp_path = tmp_path / "dmr"
    tmp_path.mkdir()
    (tmp_path / "Analog_Bridge.ini").write_text(SEEDED_ANALOG)
    (tmp_path / "MMDVM_Bridge.ini").write_text(SEEDED_MMDVM)
    bridge, audio = bridge_in(tmp_path)
    settings = DmrSettings(**{k: v for k, v in REQUEST.items() if k in DmrSettings.__dataclass_fields__})
    status = asyncio.run(bridge.save(settings))
    assert status["available"] and status["enabled"]
    analog = (tmp_path / "Analog_Bridge.ini").read_text()
    assert ini_file.section_values(analog, "USRP")["address"] == "172.28.0.10"  # the core's, not 127.0.0.1
    assert ini_file.section_values(analog, "AMBE_AUDIO")["gatewayDmrId"] == "3120001"
    assert ini_file.section_values((tmp_path / "MMDVM_Bridge.ini").read_text(), "DMR Network")["Password"] == "pw-secret-1"
    assert audio.configured == [DmrUsrpSettings("0.0.0.0", 34011, "172.28.0.30", 34012)]


def test_a_host_install_still_seeds_the_files_itself(tmp_path):
    audio = QuietAudio()
    bridge = DmrBridge(tmp_path, audio, restart=None, usrp=None)
    assert asyncio.run(bridge.status())["available"] is True
    assert (tmp_path / "Analog_Bridge.ini").exists()


def test_a_container_bridge_s_programs_are_what_its_supervisor_last_wrote(tmp_path):
    from api.dmr_restart import DmrRestartControl, DmrRestartSettings

    folder, control = tmp_path / "dmr", tmp_path / "control"
    folder.mkdir()
    control.mkdir()
    restart = DmrRestartControl(DmrRestartSettings(control / "request", control))
    bridge = DmrBridge(folder, QuietAudio(), restart=restart, usrp=None, bridge_seeds_config=True)
    unknown = {"analog_bridge": None, "mmdvm_bridge": None, "md380_emu": None}
    assert asyncio.run(bridge.status())["services"] == unknown  # nothing written yet: not guessed
    (control / "programs.json").write_text(json.dumps({"running": {"md380_emu": True, "analog_bridge": True, "mmdvm_bridge": False}}))
    assert asyncio.run(bridge.status())["services"] == {"analog_bridge": True, "mmdvm_bridge": False, "md380_emu": True}
    (control / "programs.json").write_text("not json")
    assert asyncio.run(bridge.status())["services"] == unknown
    (control / "programs.json").write_text(json.dumps({"running": {"analog_bridge": "yes"}}))
    assert asyncio.run(bridge.status())["services"] == unknown  # only a true or false is believed
