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
from api.service import RepeaterService
from api.support_bundle import support_bundle_name, write_support_bundle
from api.users import UserStore


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
            stdout = "journal line\n"
            stderr = ""

        assert cmd[:3] == ["journalctl", "-u", "moreopenrepeater"]
        return Result()

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
            "system.json",
            "moreopenrepeater.log",
            "journal.txt",
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
