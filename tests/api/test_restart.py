import base64
from pathlib import Path

from fastapi.testclient import TestClient

from api.app import create_app
from api.auth import AuthSettings
from api.restart import RestartControl, RestartSettings
from api.service import RepeaterService
from api.users import UserStore
from controller.state_machine import RepeaterConfig


def basic(username, password):
    return {"Authorization": "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()}


def make_client(tmp_path: Path, available=True, users=None):
    request = tmp_path / "restart-request"
    restarter = RestartControl(RestartSettings(request) if available else None)
    app = create_app(
        service=RepeaterService(config=RepeaterConfig()),
        start_background_tick=False,
        log_path=tmp_path / "test.log",
        auth_settings=AuthSettings("admin", "hunter2"),
        users=users if users is not None else UserStore(),
        restarter=restarter,
    )
    return TestClient(app), request, restarter


def test_restart_unavailable_without_install(tmp_path):
    client, request, _ = make_client(tmp_path, available=False)
    assert client.get("/api/restart", headers=basic("admin", "hunter2")).json() == {
        "available": False,
        "requested": False,
    }
    assert client.post("/api/restart", headers=basic("admin", "hunter2")).status_code == 409
    assert not request.exists()


def test_restart_writes_the_request_file(tmp_path):
    client, request, restarter = make_client(tmp_path)
    body = client.post("/api/restart", headers=basic("admin", "hunter2")).json()
    assert body == {"available": True, "requested": True}
    assert request.read_text() == "restart\n"
    assert client.post("/api/restart", headers=basic("admin", "hunter2")).status_code == 409
    assert restarter.status()["requested"] is True


def test_restart_is_admin_only(tmp_path):
    users = UserStore()
    users.add("alice", "password1", "admin")
    users.add("olive", "password2", "operator")
    client, *_ = make_client(tmp_path, users=users)
    assert client.get("/api/restart", headers=basic("olive", "password2")).status_code == 403
    assert client.post("/api/restart", headers=basic("olive", "password2")).status_code == 403
