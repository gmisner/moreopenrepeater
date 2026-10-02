import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from controller.state_machine import RECEIVING, RepeaterConfig

from api.app import create_app
from api.assets import AudioAssetStore
from api.service import RepeaterService


def make_client():
    clock = {"now": 0.0}
    service = RepeaterService(config=RepeaterConfig(hang_time=1.0, tot_duration=5.0), clock=lambda: clock["now"])
    tmp_dir = Path(tempfile.mkdtemp())
    assets_store = AudioAssetStore(tmp_dir / "audio")
    app = create_app(
        service=service,
        start_background_tick=False,
        assets_store=assets_store,
        log_path=tmp_dir / "test.log",
    )
    return TestClient(app), service, clock


def test_get_status_returns_idle_initially():
    client, service, clock = make_client()

    response = client.get("/api/status")

    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "idle"
    assert body["ptt_active"] is False


def test_get_config_returns_defaults():
    client, service, clock = make_client()

    response = client.get("/api/config")

    assert response.status_code == 200
    assert response.json()["hang_time"] == 1.0


def test_put_config_updates_and_returns_new_values():
    client, service, clock = make_client()

    response = client.put("/api/config", json={"hang_time": 42.0})

    assert response.status_code == 200
    assert response.json()["hang_time"] == 42.0
    assert service.config.hang_time == 42.0


def test_put_config_can_clear_require_ctcss_hz():
    client, service, clock = make_client()
    service.update_config(require_ctcss_hz=100.0)

    response = client.put("/api/config", json={"clear_require_ctcss_hz": True})

    assert response.status_code == 200
    assert response.json()["require_ctcss_hz"] is None


def test_simulate_cos_endpoint_transitions_state():
    client, service, clock = make_client()

    response = client.post("/api/simulate/cos", json={"active": True})

    assert response.status_code == 200
    assert response.json()["state"] == RECEIVING


def test_simulate_dtmf_endpoint_accepts_digit():
    client, service, clock = make_client()

    response = client.post("/api/simulate/dtmf", json={"digit": "5"})

    assert response.status_code == 200


def test_simulate_remote_keyed_endpoint_tracks_node():
    client, service, clock = make_client()

    response = client.post("/api/simulate/remote-keyed", json={"node_id": "1999", "keyed": True})

    assert response.status_code == 200
    assert response.json()["linked_nodes"] == ["1999"]


def test_websocket_status_streams_updates():
    client, service, clock = make_client()

    with client.websocket_connect("/ws/status") as websocket:
        initial = websocket.receive_json()
        assert initial["state"] == "idle"

        client.post("/api/simulate/cos", json={"active": True})

        update = websocket.receive_json()
        assert update["state"] == RECEIVING


def test_index_serves_dashboard_html():
    client, service, clock = make_client()

    response = client.get("/")

    assert response.status_code == 200
    assert "moreopenrepeater" in response.text


def test_list_macros_starts_empty():
    client, service, clock = make_client()

    response = client.get("/api/macros")

    assert response.status_code == 200
    assert response.json() == []


def test_add_macro_returns_it_in_the_list():
    client, service, clock = make_client()

    response = client.post(
        "/api/macros",
        json={"pattern": "*81", "description": "disconnect all", "command": "disconnect_all", "node_id": "*"},
    )

    assert response.status_code == 200
    assert response.json() == [
        {"pattern": "*81", "description": "disconnect all", "command": "disconnect_all", "node_id": "*", "action": "link", "needs_code": False}
    ]


def test_add_macro_with_same_pattern_replaces_the_existing_one():
    client, service, clock = make_client()
    client.post("/api/macros", json={"pattern": "*81", "description": "old", "command": "old_cmd"})

    response = client.post("/api/macros", json={"pattern": "*81", "description": "new", "command": "new_cmd"})

    assert len(response.json()) == 1
    assert response.json()[0]["command"] == "new_cmd"


def test_delete_macro_removes_it():
    client, service, clock = make_client()
    client.post("/api/macros", json={"pattern": "*81", "description": "x", "command": "x"})

    response = client.delete("/api/macros/*81")

    assert response.status_code == 200
    assert response.json() == []


def test_delete_unknown_macro_returns_404():
    client, service, clock = make_client()

    response = client.delete("/api/macros/*99")

    assert response.status_code == 404


def test_snapshot_round_trips_through_the_http_api():
    client, service, clock = make_client()
    client.put("/api/config", json={"hang_time": 42.0})
    client.post("/api/macros", json={"pattern": "*81", "description": "x", "command": "disconnect_all"})

    exported = client.get("/api/snapshot").json()
    assert exported["config"]["hang_time"] == 42.0
    assert exported["macros"] == [
        {"pattern": "*81", "description": "x", "command": "disconnect_all", "node_id": "", "action": "link", "needs_code": False}
    ]

    # A fresh service/client should pick up the imported snapshot.
    fresh_client, fresh_service, _ = make_client()
    response = fresh_client.post("/api/snapshot", json=exported)

    assert response.status_code == 200
    assert response.json() == exported
    assert fresh_service.config.hang_time == 42.0


def test_upload_asset_stores_and_lists_it():
    client, service, clock = make_client()

    response = client.post(
        "/api/assets",
        data={"kind": "courtesy_tone"},
        files={"file": ("beep.wav", b"RIFF....WAVEfmt ", "audio/wav")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "courtesy_tone"
    assert body["filename"] == "beep.wav"

    listed = client.get("/api/assets").json()
    assert listed == [body]


def test_upload_asset_rejects_non_wav_files():
    client, service, clock = make_client()

    response = client.post(
        "/api/assets",
        data={"kind": "courtesy_tone"},
        files={"file": ("beep.mp3", b"not-a-wav", "audio/mpeg")},
    )

    assert response.status_code == 400


def test_get_asset_audio_returns_the_uploaded_content():
    client, service, clock = make_client()
    uploaded = client.post(
        "/api/assets",
        data={"kind": "id"},
        files={"file": ("mycall.wav", b"RIFF....WAVEfmt ", "audio/wav")},
    ).json()

    response = client.get(f"/api/assets/{uploaded['id']}/audio")

    assert response.status_code == 200
    assert response.content == b"RIFF....WAVEfmt "


def test_delete_asset_removes_it():
    client, service, clock = make_client()
    uploaded = client.post(
        "/api/assets",
        data={"kind": "custom"},
        files={"file": ("clip.wav", b"data", "audio/wav")},
    ).json()

    response = client.delete(f"/api/assets/{uploaded['id']}")

    assert response.status_code == 200
    assert client.get("/api/assets").json() == []


def test_delete_unknown_asset_returns_404():
    client, service, clock = make_client()

    response = client.delete("/api/assets/does-not-exist")

    assert response.status_code == 404


def test_logs_endpoint_has_no_activity_before_any_action():
    client, service, clock = make_client()

    response = client.get("/api/logs")

    assert response.status_code == 200
    assert not any("moreopenrepeater.controller" in line for line in response.json())


def test_logs_endpoint_reflects_a_real_log_entry_after_an_action():
    client, service, clock = make_client()

    client.post("/api/simulate/cos", json={"active": True})

    response = client.get("/api/logs")

    assert response.status_code == 200
    lines = response.json()
    assert any("simulate_cos" in line for line in lines)
    assert any("state idle -> receiving" in line for line in lines)


def test_logs_endpoint_respects_the_lines_query_param():
    client, service, clock = make_client()
    for digit in "0123456789":
        client.post("/api/simulate/dtmf", json={"digit": digit})

    response = client.get("/api/logs", params={"lines": 3})

    assert len(response.json()) == 3


def test_static_files_are_served_with_no_cache_so_upgrades_take_effect():
    client, service, clock = make_client()

    assert client.get("/style.css").headers["cache-control"] == "no-cache"
    assert client.get("/").headers["cache-control"] == "no-cache"
