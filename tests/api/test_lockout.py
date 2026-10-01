import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from controller.macros import Macro
from controller.state_machine import LOCKOUT, RECEIVING, RepeaterConfig

from api.activity import ActivityRecorder, ActivityStore
from api.app import create_app
from api.assets import AudioAssetStore
from api.service import RepeaterService


def make_client(macros=()):
    clock = {"now": 0.0}
    service = RepeaterService(
        config=RepeaterConfig(tot_duration=10.0, lockout_timeouts=2, lockout_window=60.0, lockout_clear_after=20.0),
        macros=list(macros),
        clock=lambda: clock["now"],
        activity=ActivityRecorder(ActivityStore()),
    )
    tmp_dir = Path(tempfile.mkdtemp())
    app = create_app(
        service=service, start_background_tick=False, assets_store=AudioAssetStore(tmp_dir / "audio"), log_path=tmp_dir / "test.log"
    )
    return TestClient(app), service, clock


def lock_out(service, clock):
    service.simulate_cos(True)
    for t in (10.0, 20.0):
        clock["now"] = t
        service.tick()
    assert service.controller.state == LOCKOUT


def test_status_reports_the_lockout_and_activity_counts_it():
    client, service, clock = make_client()
    changes = []
    service.lockout_hook = changes.append

    lock_out(service, clock)

    status = client.get("/api/status").json()
    assert status["state"] == "lockout" and status["locked_out"] is True
    assert changes == [True]
    summary = client.get("/api/activity/summary?days=1").json()
    assert summary["lockouts"] == 1


def test_clearing_from_the_dashboard():
    client, service, clock = make_client()
    changes = []
    service.lockout_hook = changes.append
    assert client.post("/api/lockout/clear").status_code == 409

    lock_out(service, clock)
    response = client.post("/api/lockout/clear")

    assert response.status_code == 200
    assert response.json()["locked_out"] is False
    assert service.controller.state == RECEIVING  # the carrier is still there
    assert changes == [True, False]


def test_clearing_over_dtmf():
    client, service, clock = make_client([Macro("*55", "clear lockout", action="lockout_clear")])
    lock_out(service, clock)

    for digit in "*55":
        service.simulate_dtmf(digit)

    assert not service.controller.locked_out
    assert "tts:Lockout cleared" in service.controller.queued_announcements


def test_lockout_settings_round_trip():
    client, service, clock = make_client()

    response = client.put("/api/config", json={"lockout_timeouts": 0, "lockout_window": 1200, "lockout_clear_after": 90})

    assert response.status_code == 200
    assert service.config.lockout_timeouts == 0 and service.config.lockout_clear_after == 90
    assert client.put("/api/config", json={"lockout_clear_after": 1}).status_code == 422
