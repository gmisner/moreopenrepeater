from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.dmr import DmrBridge
from api.dmr_audio import DmrAudio
from api.dmr_restart import DmrRestartControl, DmrRestartSettings
from api.service import RepeaterService
from controller.config import RepeaterConfig


def make_bridge(tmp_path: Path, *, directory: Path | None = None, restart: DmrRestartControl | None = None):
    service = RepeaterService(config=RepeaterConfig(callsign="W1AW"))
    audio = DmrAudio(service, None)
    return DmrBridge(directory, audio, restart=restart, usrp=None), service, audio


def test_install_writes_request_when_not_available(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("MOREOPENREPEATER_AUTH_USER", raising=False)
    monkeypatch.delenv("MOREOPENREPEATER_AUTH_PASSWORD", raising=False)
    request = tmp_path / "dmr-restart-request"
    status_dir = tmp_path / "status"
    status_dir.mkdir()
    clock = {"now": 1000.0}
    restart = DmrRestartControl(DmrRestartSettings(request, status_dir), clock=lambda: clock["now"])
    bridge, service, audio = make_bridge(tmp_path, directory=None, restart=restart)
    app = create_app(service=service, start_background_tick=False, dmr=bridge, dmr_audio=audio)
    client = TestClient(app)

    body = client.get("/api/dmr").json()
    assert body["available"] is False
    assert body["restart"]["available"] is True

    response = client.post("/api/dmr/install")
    assert response.status_code == 200, response.text
    assert request.read_text().strip() == "install"
    assert response.json()["restart"]["requested"] is True
    assert response.json()["restart"]["last_action"] == "install"
    assert response.json()["restart"]["state"] == "requested"

    again = client.post("/api/dmr/install")
    assert again.status_code == 409
    assert "already" in again.json()["detail"].lower()


def test_install_rejected_when_already_installed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("MOREOPENREPEATER_AUTH_USER", raising=False)
    monkeypatch.delenv("MOREOPENREPEATER_AUTH_PASSWORD", raising=False)
    request = tmp_path / "dmr-restart-request"
    restart = DmrRestartControl(DmrRestartSettings(request, tmp_path / "status"))
    bridge, service, audio = make_bridge(tmp_path, directory=tmp_path, restart=restart)
    app = create_app(service=service, start_background_tick=False, dmr=bridge, dmr_audio=audio)
    client = TestClient(app)
    assert client.post("/api/dmr/install").status_code == 409
    assert not request.exists()


def test_helper_status_reads_install_progress(tmp_path: Path):
    request = tmp_path / "req"
    status_dir = tmp_path / "st"
    status_dir.mkdir()
    now = 1_700_000_000.0
    control = DmrRestartControl(DmrRestartSettings(request, status_dir), clock=lambda: now)
    (status_dir / "status.json").write_text(
        f'{{"state":"running","action":"install","started_at":{now - 10},"message":"Installing…"}}'
    )
    status = control.status()
    assert status["state"] == "running"
    assert status["message"] == "Installing…"
    assert status["requested"] is True
