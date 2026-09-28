import base64
import importlib
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.audit import AuditLog
from api.auth import AuthSettings
from api.persistence import StateStore
from api.service import RepeaterService
from api.users import UserError, UserStore, check_password, hash_password
from controller.events import RunAction


@pytest.fixture(autouse=True)
def no_login_failure_delay(monkeypatch):
    monkeypatch.setattr(importlib.import_module("api.app"), "LOGIN_FAILURE_DELAY_SECONDS", 0)


def make_client(auth_settings=None, users=None):
    tmp = Path(tempfile.mkdtemp())
    audit = AuditLog()
    service = RepeaterService()
    app = create_app(
        service=service,
        start_background_tick=False,
        log_path=tmp / "t.log",
        auth_settings=auth_settings,
        users=users if users is not None else UserStore(),
        audit=audit,
    )
    return TestClient(app), audit, service


def basic(username, password):
    return {"Authorization": "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()}


def login(client, username, password):
    return client.post("/api/login", json={"username": username, "password": password})


def test_password_hashing_round_trip():
    stored = hash_password("correct horse")
    assert stored.startswith("scrypt$")
    assert check_password("correct horse", stored)
    assert not check_password("wrong", stored)
    assert not check_password("x", "garbage")


def test_store_persists_and_protects_the_last_admin(tmp_path):
    store = UserStore(StateStore(tmp_path / "users.json"))
    with pytest.raises(UserError, match="first user must be an admin"):
        store.add("bob", "password1", "viewer")
    store.add("alice", "password1", "admin")
    store.add("bob", "password2", "viewer")
    with pytest.raises(UserError, match="already exists"):
        store.add("bob", "password3", "viewer")
    with pytest.raises(UserError, match="admin"):
        store.update("alice", role="operator")
    with pytest.raises(UserError, match="admin"):
        store.delete("alice")

    reloaded = UserStore(StateStore(tmp_path / "users.json"))
    assert [(u.username, u.role) for u in reloaded.list()] == [("alice", "admin"), ("bob", "viewer")]
    assert reloaded.authenticate("bob", "password2").role == "viewer"
    assert reloaded.authenticate("bob", "nope") is None
    assert reloaded.authenticate("nobody", "password2") is None
    assert "password2" not in (tmp_path / "users.json").read_text()


def test_env_admin_means_any_role_can_come_first():
    store = UserStore(reserved_username="admin")
    store.add("bob", "password1", "viewer")
    store.delete("bob")
    with pytest.raises(UserError, match="built-in"):
        store.add("admin", "password1", "viewer")


def test_adding_first_user_turns_sign_in_on():
    client, _audit, _service = make_client()
    assert client.get("/api/status").status_code == 200

    response = client.post("/api/users", json={"username": "alice", "password": "password1", "role": "admin"})
    assert response.status_code == 200
    assert client.get("/api/status").status_code == 401
    assert client.get("/api/session").json()["auth_required"] is True

    assert login(client, "alice", "password1").json()["role"] == "admin"
    assert client.get("/api/status").status_code == 200


def test_viewer_is_read_only():
    users = UserStore(reserved_username="admin")
    users.add("val", "password1", "viewer")
    client, _audit, _service = make_client(AuthSettings("admin", "hunter2"), users)
    login(client, "val", "password1")

    assert client.get("/api/config").status_code == 200
    assert client.put("/api/config", json={"hang_time": 2.0}).status_code == 403
    assert client.post("/api/simulate/cos", json={"active": True}).status_code == 403
    assert client.get("/api/users").status_code == 403
    with client.websocket_connect("/ws/status") as ws:
        assert ws.receive_json()["state"] == "idle"


def test_operator_changes_settings_but_not_users():
    users = UserStore(reserved_username="admin")
    users.add("olly", "password1", "operator")
    client, _audit, _service = make_client(AuthSettings("admin", "hunter2"), users)
    headers = basic("olly", "password1")

    assert client.put("/api/config", json={"hang_time": 2.0}, headers=headers).status_code == 200
    assert client.get("/api/users", headers=headers).status_code == 403
    assert client.get("/api/audit", headers=headers).status_code == 403


def test_role_change_and_deletion_apply_to_live_sessions():
    users = UserStore(reserved_username="admin")
    users.add("olly", "password1", "operator")
    client, _audit, _service = make_client(AuthSettings("admin", "hunter2"), users)
    login(client, "olly", "password1")
    assert client.put("/api/config", json={"hang_time": 2.0}).status_code == 200

    admin = basic("admin", "hunter2")
    client.put("/api/users/olly", json={"role": "viewer"}, headers=admin)
    assert client.put("/api/config", json={"hang_time": 2.5}).status_code == 403

    client.delete("/api/users/olly", headers=admin)
    assert client.get("/api/status").status_code == 401


def test_user_admin_endpoints():
    client, _audit, _service = make_client(AuthSettings("admin", "hunter2"))
    admin = basic("admin", "hunter2")

    listed = client.post("/api/users", json={"username": "bob", "password": "password1", "role": "operator"}, headers=admin)
    assert listed.json() == [
        {"username": "admin", "role": "admin", "builtin": True},
        {"username": "bob", "role": "operator", "builtin": False},
    ]
    assert client.post("/api/users", json={"username": "bob", "password": "password1", "role": "viewer"}, headers=admin).status_code == 400
    assert client.post("/api/users", json={"username": "eve", "password": "short", "role": "viewer"}, headers=admin).status_code == 422
    assert client.put("/api/users/bob", json={"password": "password9"}, headers=admin).status_code == 200
    assert login(client, "bob", "password9").status_code == 200
    assert client.put("/api/users/ghost", json={"role": "viewer"}, headers=admin).status_code == 404
    assert client.delete("/api/users/bob", headers=admin).status_code == 200


def test_audit_log_records_who_changed_what_but_not_passwords():
    client, audit, service = make_client(AuthSettings("admin", "hunter2"))
    login(client, "admin", "wrong-password")
    login(client, "admin", "hunter2")
    client.put("/api/config", json={"hang_time": 4.0})
    client.post("/api/users", json={"username": "bob", "password": "secretpass", "role": "viewer"})
    client.post("/api/audio/preview", json={"clip": "courtesy_tone"})
    service._run_action(RunAction("time"))

    entries = client.get("/api/audit").json()
    summary = [(e["actor"], e["action"], e["status"]) for e in reversed(entries)]
    assert summary == [
        ("admin", "POST /api/login", 401),
        ("admin", "POST /api/login", 200),
        ("admin", "PUT /api/config", 200),
        ("admin", "POST /api/users", 200),
        ("DTMF", "DTMF time", 0),
    ]
    assert entries[2]["detail"] == "hang_time: 3.0 → 4.0"
    assert entries[1]["detail"] == "bob (viewer)"
    assert all("secretpass" not in e["detail"] and "hunter2" not in e["detail"] for e in entries)
