import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from controller.announcements import Announcement
from controller.state_machine import ANNOUNCING, IDLE, RepeaterConfig

from api.app import create_app
from api.assets import AudioAssetStore
from api.persistence import StateStore
from api.service import RepeaterService
from playout.renderer import ClipRenderer

RATE = 8000
START = datetime(2026, 9, 28, 9, 0)


class FakeTTS:
    name = "fake"

    def __init__(self):
        self.spoken = []

    def synthesize(self, text, voice=""):
        self.spoken.append(text)
        return np.full(2 * RATE, 0.1, dtype=np.float32), RATE  # 2 seconds


def make_client(tts=None, state_path=None):
    tmp = Path(tempfile.mkdtemp())
    assets = AudioAssetStore(tmp / "audio")
    renderer = ClipRenderer(assets.path_for, tts=tts or FakeTTS(), sample_rate=RATE)
    clock = {"now": 0.0, "wall": START}
    service = RepeaterService(
        config=RepeaterConfig(callsign="W1AW"),
        clock=lambda: clock["now"],
        wall_clock=lambda: clock["wall"],
        renderer=renderer,
        state_store=StateStore(state_path) if state_path else None,
    )
    app = create_app(
        service=service, start_background_tick=False, assets_store=assets, log_path=tmp / "t.log", renderer=renderer
    )
    return TestClient(app), service, clock


NET_REMINDER = {"name": "Net reminder", "message": "Net tonight at 8pm on {callsign}", "every_minutes": 30}


def test_create_list_update_delete_round_trip():
    client, service, clock = make_client()

    created = client.post("/api/announcements", json=NET_REMINDER).json()
    assert created["name"] == "Net reminder"
    assert created["next_run"] == (START + timedelta(minutes=30)).isoformat()
    assert [a["id"] for a in client.get("/api/announcements").json()] == [created["id"]]

    updated = client.put(f"/api/announcements/{created['id']}", json={**NET_REMINDER, "enabled": False}).json()
    assert updated["enabled"] is False
    assert updated["next_run"] is None

    assert client.delete(f"/api/announcements/{created['id']}").status_code == 200
    assert client.get("/api/announcements").json() == []


def test_update_and_delete_unknown_announcement_is_404():
    client, *_ = make_client()

    assert client.put("/api/announcements/nope", json=NET_REMINDER).status_code == 404
    assert client.delete("/api/announcements/nope").status_code == 404
    assert client.post("/api/announcements/nope/play").status_code == 404


def test_validation_rejects_empty_or_malformed_announcements():
    client, *_ = make_client()

    assert client.post("/api/announcements", json={"name": "x", "message": " "}).status_code == 422
    assert client.post("/api/announcements", json={"name": "x", "message": "hi", "kind": "weekly"}).status_code == 422
    bad_time = {"name": "x", "message": "hi", "kind": "weekly", "times": ["25:00"]}
    assert client.post("/api/announcements", json=bad_time).status_code == 422
    assert client.post("/api/announcements", json={"name": "x", "message": "hi", "days": [7]}).status_code == 422


def test_weekly_times_are_normalized_and_sorted():
    client, *_ = make_client()

    body = {"name": "x", "message": "hi", "kind": "weekly", "times": ["19:30", "8:05", "19:30"]}
    created = client.post("/api/announcements", json=body).json()

    assert created["times"] == ["08:05", "19:30"]
    assert created["next_run"] == datetime(2026, 9, 28, 19, 30).isoformat()


def test_announcement_with_unknown_asset_is_rejected():
    client, *_ = make_client()

    response = client.post("/api/announcements", json={"name": "x", "asset_id": "missing"})

    assert response.status_code == 400


def test_play_now_renders_then_transmits_when_idle():
    tts = FakeTTS()
    client, service, clock = make_client(tts=tts)
    announcement = client.post("/api/announcements", json=NET_REMINDER).json()

    assert client.post(f"/api/announcements/{announcement['id']}/play").status_code == 200
    assert tts.spoken == ["Net tonight at 8pm on W 1 A W"]

    service.tick()
    assert service.controller.state == ANNOUNCING
    assert service.ptt_active
    clock["now"] = 2.0  # the rendered clip is 2 s long
    service.tick()
    assert service.controller.state == IDLE
    assert not service.ptt_active


def test_play_now_without_tts_is_503():
    class BrokenTTS(FakeTTS):
        def synthesize(self, text, voice=""):
            from playout.tts import TTSError

            raise TTSError("engine exploded")

    client, *_ = make_client(tts=BrokenTTS())
    announcement = client.post("/api/announcements", json=NET_REMINDER).json()

    response = client.post(f"/api/announcements/{announcement['id']}/play")

    assert response.status_code == 503
    assert "exploded" in response.json()["detail"]


def test_service_reports_due_clips_on_schedule():
    client, service, clock = make_client()
    client.post("/api/announcements", json=NET_REMINDER)

    assert service.due_announcement_clips() == []
    clock["wall"] = START + timedelta(minutes=30)
    assert service.due_announcement_clips() == ["tts:Net tonight at 8pm on {callsign}"]
    assert service.due_announcement_clips() == []


def test_asset_announcement_uses_the_uploaded_clip():
    _, service, _ = make_client()

    clip = service.announcement_clip(Announcement(id="a", name="a", asset_id="abc123"))

    assert clip == "asset:abc123"


def test_announcements_persist_and_appear_in_snapshots():
    state_path = Path(tempfile.mkdtemp()) / "state.json"
    client, *_ = make_client(state_path=state_path)
    created = client.post("/api/announcements", json=NET_REMINDER).json()

    assert client.get("/api/snapshot").json()["announcements"][0]["id"] == created["id"]

    reloaded_client, *_ = make_client(state_path=state_path)
    assert [a["name"] for a in reloaded_client.get("/api/announcements").json()] == ["Net reminder"]


def test_snapshot_import_replaces_announcements_and_tolerates_old_backups():
    client, *_ = make_client()
    client.post("/api/announcements", json=NET_REMINDER)
    snapshot = client.get("/api/snapshot").json()

    old_backup = {"config": snapshot["config"], "macros": []}
    assert client.post("/api/snapshot", json=old_backup).json()["announcements"] == []

    assert len(client.post("/api/snapshot", json=snapshot).json()["announcements"]) == 1
