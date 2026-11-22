import importlib
import io
import json
import tempfile
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.audit import AuditLog
from api.auth import AuthSettings
from api.persistence import StateStore
from api.remote_core import RemoteCore
from api.service import RepeaterService
from api.service.core_handle import CoreUnavailable
from api.support_bundle import support_bundle_name, write_support_bundle
from api.users import UserStore
from controller.state_machine import RepeaterConfig


@pytest.fixture(autouse=True)
def no_login_failure_delay(monkeypatch):
    monkeypatch.setattr(importlib.import_module("api.app"), "LOGIN_FAILURE_DELAY_SECONDS", 0)


class Repeater:
    def __init__(self, auth: bool = False) -> None:
        self.dir = Path(tempfile.mkdtemp())
        self.log_path = self.dir / "t.log"
        self.log_path.write_text("INFO boot\nWARNING soft\nERROR hard\n")
        self.service = RepeaterService(state_store=StateStore(self.dir / "state.json"))
        self.users = UserStore(StateStore(self.dir / "users.json"))
        self.audit = AuditLog(self.dir / "audit.db")
        app = create_app(
            service=self.service,
            start_background_tick=False,
            log_path=self.log_path,
            auth_settings=AuthSettings("owner", "hunter2") if auth else None,
            users=self.users,
            audit=self.audit,
        )
        self.client = TestClient(app)
        if auth:
            self.client.post("/api/login", json={"username": "owner", "password": "hunter2"})


def test_support_bundle_name_slugs_the_callsign():
    assert support_bundle_name("W1AW/R", 1_700_000_000.0).startswith("moreopenrepeater-support-w1aw-r-")
    assert support_bundle_name("", 1_700_000_000.0).startswith("moreopenrepeater-support-repeater-")


def test_write_support_bundle_holds_logs_and_settings_without_secrets(tmp_path, monkeypatch):
    service = RepeaterService(state_store=StateStore(tmp_path / "state.json"))
    service.update_config(callsign="W1AW")
    log_path = tmp_path / "app.log"
    log_path.write_text("line one\nline two\n")

    def fake_run(cmd, **_kwargs):
        class Result:
            returncode = 0
            stdout = ""
            stderr = ""

        if cmd[:3] == ["journalctl", "-u", "moreopenrepeater"]:
            Result.stdout = "journal line\n"
        elif cmd[:2] == ["journalctl", "-k"]:
            Result.stdout = "kernel: usb 1-1.3: reset full-speed USB device\nother line\n"
        elif cmd[0] == "lsusb":
            Result.stdout = "Bus 001 Device 023: ID 1209:7388 Generic All-In-One-Cable\n"
        elif cmd[0] == "udevadm":
            Result.stdout = "DEVNAME=/dev/hidraw4\nID_MODEL=All-In-One-Cable\nOTHER=x\n"
        elif cmd[:2] == ["amixer", "-c"]:
            Result.stdout = (
                "Simple mixer control 'Mic',0\n"
                "  Capabilities: cvolume\n"
                "  Limits: Capture 0 - 16\n"
                "  Capture channels: Mono\n"
                "  Mono: Capture 8 [50%] [12.00dB]\n"
            )
        return Result()

    service.update_config(audio_input_device="USB Audio (hw:2,0)", audio_output_device="USB Audio (hw:2,0)")
    path = tmp_path / "bundle.zip"
    write_support_bundle(
        path,
        service=service,
        log_path=log_path,
        repo_dir=tmp_path,
        update_channel="dev",
        live_audio_status={"running": False, "error": None},
        system_readings={"temperature_c": 42.0},
        updater=None,
        now=1_700_000_000.0,
        receiver_openings=[{"duration": 1.2, "peak_db": -5.0, "dtmf_digits": "*11"}],
        audio_glitches=[{"counter": "output_underflows", "count": 1}],
        last_unexpected_stop={"watchdog": "audio engine"},
        audio_devices=[{"name": "USB Audio (hw:2,0)", "inputs": 1, "outputs": 1}],
        serial_ports=[{"path": "/dev/serial/by-id/usb-Digirig", "target": "/dev/ttyUSB0"}],
        cm108_interfaces=[{"path": "/dev/hidraw4", "name": "C-Media"}],
        cm108_path="/dev/hidraw4",
        run=fake_run,
    )

    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        assert names == {
            "README.txt",
            "version.json",
            "config.json",
            "status.json",
            "audio-engine.json",
            "devices.json",
            "openings.json",
            "glitches.json",
            "health.json",
            "system.json",
            "usb.txt",
            "moreopenrepeater.log",
            "journal.txt",
            "event-history.json",
            "update.log",
        }
        assert "users" not in names
        assert "line one" in archive.read("moreopenrepeater.log").decode()
        assert "journal line" in archive.read("journal.txt").decode()
        config = json.loads(archive.read("config.json"))
        assert config["config"]["callsign"] == "W1AW"
        assert "password" not in json.dumps(config).lower()
        assert json.loads(archive.read("audio-engine.json"))["running"] is False
        assert json.loads(archive.read("system.json"))["temperature_c"] == 42.0
        assert json.loads(archive.read("openings.json"))[0]["dtmf_digits"] == "*11"
        assert json.loads(archive.read("glitches.json"))[0]["counter"] == "output_underflows"
        assert json.loads(archive.read("health.json"))["last_unexpected_stop"]["watchdog"] == "audio engine"
        devices = json.loads(archive.read("devices.json"))
        assert devices["cm108_path"] == "/dev/hidraw4"
        assert devices["serial_ports"][0]["path"].endswith("Digirig")
        assert devices["audio_devices"][0]["name"].startswith("USB Audio")
        assert devices["mixer"]["2"]["volumes"][0]["control"] == "Mic"
        usb = archive.read("usb.txt").decode()
        assert "All-In-One-Cable" in usb
        assert "reset full-speed USB device" in usb
        assert "devices.json" in archive.read("README.txt").decode()
        assert "usb.txt" in archive.read("README.txt").decode()
        assert "does not include passwords" in archive.read("README.txt").decode()


def test_support_bundle_download_is_admin_only_and_audited():
    r = Repeater(auth=True)
    r.service.update_config(callsign="K3TEG")
    response = r.client.get("/api/support-bundle")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/zip")
    assert response.headers["content-disposition"].startswith(
        'attachment; filename="moreopenrepeater-support-k3teg-'
    )
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert "moreopenrepeater.log" in archive.namelist()
        assert "ERROR hard" in archive.read("moreopenrepeater.log").decode()

    assert r.audit.recent()[0].action == "download support bundle"

    r.users.add("op", "operator pass", "operator")
    operator = TestClient(r.client.app)
    operator.post("/api/login", json={"username": "op", "password": "operator pass"})
    assert operator.get("/api/support-bundle").status_code == 403


# -- the core is not reachable: the bundle is needed most then, and everything API-side still goes in --------


def _quiet_run(cmd, **_kwargs):
    class Result:
        returncode = 0
        stdout = "kernel: usb 1-1.3: reset full-speed USB device\n" if cmd[:2] == ["journalctl", "-k"] else "journal line\n"
        stderr = ""

    return Result()


def _write(tmp_path, service):
    log_path = tmp_path / "app.log"
    log_path.write_text("api line one\n")
    path = tmp_path / "bundle.zip"
    write_support_bundle(
        path, service=service, log_path=log_path, repo_dir=tmp_path, update_channel="dev", live_audio_status=None,
        system_readings={"temperature_c": 40.0}, updater=None, now=1_700_000_000.0,
        cm108_interfaces=[{"path": "/dev/hidraw4", "name": "C-Media"}], run=_quiet_run,
    )
    return path


def test_a_stale_core_still_gets_a_bundle_with_a_note_where_the_settings_were(tmp_path):
    remote = RemoteCore(tmp_path / "no-core.sock")
    remote._config = RepeaterConfig(callsign="W1AW", ptt_serial_device="/dev/ttyUSB0")  # read once, now not heard from
    remote._macros = []
    assert remote.stale

    with zipfile.ZipFile(_write(tmp_path, remote)) as archive:
        assert "config.json" in archive.namelist()
        settings = json.loads(archive.read("config.json"))
        assert settings == {"settings": "unavailable (core not reachable)"}
        assert "W1AW" not in archive.read("config.json").decode()  # no old copy passed off as current
        assert "api line one" in archive.read("moreopenrepeater.log").decode()
        assert "journal line" in archive.read("journal.txt").decode()
        assert "reset full-speed USB device" in archive.read("usb.txt").decode()
        assert json.loads(archive.read("devices.json"))["cm108_interfaces"][0]["path"] == "/dev/hidraw4"
        assert json.loads(archive.read("status.json"))["state"] == "core_unavailable"
        assert json.loads(archive.read("system.json"))["temperature_c"] == 40.0
        assert "could not be reached" in archive.read("README.txt").decode()


def test_a_core_that_was_never_reached_still_gets_a_bundle(tmp_path):
    remote = RemoteCore(tmp_path / "no-core.sock")  # no configuration at all yet
    with zipfile.ZipFile(_write(tmp_path, remote)) as archive:
        assert json.loads(archive.read("config.json")) == {"settings": "unavailable (core not reachable)"}
        devices = json.loads(archive.read("devices.json"))
        assert devices["selected"] == "unavailable (core not reachable)" and devices["mixer"] == {}
        assert devices["cm108_interfaces"][0]["name"] == "C-Media"
        assert "api line one" in archive.read("moreopenrepeater.log").decode()


def test_nothing_from_the_old_settings_leaks_into_the_bundle_when_the_core_is_down(tmp_path):
    remote = RemoteCore(tmp_path / "no-core.sock")
    remote._config = RepeaterConfig(callsign="W1AW", ptt_serial_device="/dev/ttyUSB-marker")
    remote._macros = []
    with zipfile.ZipFile(_write(tmp_path, remote)) as archive:
        everything = b"".join(archive.read(name) for name in archive.namelist()).decode(errors="replace")
    assert "ttyUSB-marker" not in everything and "password" not in everything.lower().replace("does not include passwords", "")


def test_the_download_works_while_the_core_is_down():
    r = Repeater(auth=True)

    def down():
        raise CoreUnavailable("the core is not being heard from, so its settings cannot be exported now")

    r.service.export_snapshot = down
    response = r.client.get("/api/support-bundle")
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert json.loads(archive.read("config.json")) == {"settings": "unavailable (core not reachable)"}
        assert "ERROR hard" in archive.read("moreopenrepeater.log").decode()
