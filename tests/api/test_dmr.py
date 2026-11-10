from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.dmr import DmrBridge
from api.dmr_audio import DmrAudio
from api.service import RepeaterService
from controller.config import RepeaterConfig


@pytest.fixture
def dmr_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MOREOPENREPEATER_DMR_DIR", str(tmp_path))
    monkeypatch.delenv("MOREOPENREPEATER_DMR_USRP_PEER", raising=False)
    monkeypatch.delenv("MOREOPENREPEATER_AUTH_USER", raising=False)
    monkeypatch.delenv("MOREOPENREPEATER_AUTH_PASSWORD", raising=False)
    service = RepeaterService(config=RepeaterConfig(callsign="W1AW"))
    audio = DmrAudio(service, None)
    bridge = DmrBridge(tmp_path, audio, restart=None, usrp=None)
    app = create_app(service=service, start_background_tick=False, dmr=bridge, dmr_audio=audio)
    return TestClient(app), bridge, tmp_path


def test_status_available_when_dir_set(dmr_env):
    client, _, _ = dmr_env
    body = client.get("/api/dmr").json()
    assert body["available"] is True
    assert body["enabled"] is False
    assert body["settings"]["callsign"] in ("", "N0CALL")
    assert body["audio"]["configured"] is False


def test_save_writes_inis_without_echoing_password(dmr_env):
    client, bridge, directory = dmr_env
    response = client.put(
        "/api/dmr",
        json={
            "callsign": "W1AW",
            "dmr_id": "3120001",
            "repeater_ssid": "01",
            "password": "secret-pass",
            "network": "brandmeister",
            "host": "3102.repeater.net",
            "port": 62031,
            "talkgroup": 3100,
            "slot": 2,
            "color_code": 1,
            "location": "Newington",
            "description": "test",
            "use_emulator": True,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["enabled"] is True
    assert body["settings"]["callsign"] == "W1AW"
    assert body["settings"]["dmr_id"] == "3120001"
    assert body["settings"]["talkgroup"] == 3100
    assert body["settings"]["has_password"] is True
    assert "password" not in body["settings"]
    # password is in the file but not in the API response
    assert "secret-pass" in (directory / "MMDVM_Bridge.ini").read_text()
    assert (directory / "enabled").exists()


def test_disable_clears_enabled_marker(dmr_env):
    client, bridge, directory = dmr_env
    assert (
        client.put(
            "/api/dmr",
            json={
                "callsign": "W1AW",
                "dmr_id": "3120001",
                "password": "secret-pass",
                "host": "3102.repeater.net",
                "talkgroup": 9,
            },
        ).status_code
        == 200
    )
    assert client.delete("/api/dmr").json()["enabled"] is False
    assert not (directory / "enabled").exists()


def test_save_keeps_password_when_blank(dmr_env):
    client, _, directory = dmr_env
    client.put(
        "/api/dmr",
        json={"callsign": "W1AW", "dmr_id": "3120001", "password": "first-secret", "host": "a.example", "talkgroup": 9},
    )
    body = client.put(
        "/api/dmr",
        json={"callsign": "W1AW", "dmr_id": "3120001", "password": "", "host": "a.example", "talkgroup": 91},
    ).json()
    assert body["settings"]["talkgroup"] == 91
    assert "first-secret" in (directory / "MMDVM_Bridge.ini").read_text()


def _enable(client: TestClient) -> None:
    assert (
        client.put(
            "/api/dmr",
            json={
                "callsign": "W1AW",
                "dmr_id": "3120001",
                "password": "secret-pass",
                "host": "3102.repeater.net",
                "talkgroup": 9,
                "slot": 2,
            },
        ).status_code
        == 200
    )


def test_select_talkgroup_updates_static_tg(dmr_env):
    client, _, directory = dmr_env
    _enable(client)
    body = client.post("/api/dmr/talkgroup", json={"talkgroup": 3100, "slot": 1}).json()
    assert body["settings"]["talkgroup"] == 3100
    assert body["settings"]["slot"] == 1
    analog = (directory / "Analog_Bridge.ini").read_text()
    assert "txTg = 3100" in analog
    assert "txTs = 1" in analog
    mmdvm = (directory / "MMDVM_Bridge.ini").read_text()
    assert "Slot1 = 1" in mmdvm
    assert "Slot2 = 0" in mmdvm


def test_select_talkgroup_requires_enabled(dmr_env):
    client, _, _ = dmr_env
    assert client.post("/api/dmr/talkgroup", json={"talkgroup": 3100}).status_code == 502


def test_dmr_talkgroup_favorites_on_config(dmr_env):
    client, _, _ = dmr_env
    assert client.put("/api/config", json={"dmr_talkgroup_favorites": [{"talkgroup": "abc"}]}).status_code == 422
    config = client.put(
        "/api/config",
        json={"dmr_talkgroup_favorites": [{"talkgroup": 3100, "name": "BM USA", "slot": 2}]},
    ).json()
    assert config["dmr_talkgroup_favorites"] == [{"talkgroup": 3100, "name": "BM USA", "slot": 2}]
