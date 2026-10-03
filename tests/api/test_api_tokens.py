import base64
import json
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from api.app import create_app
from api.audit import AuditLog
from api.auth import AuthSettings
from api.persistence import StateStore
from api.service import RepeaterService
from api.tokens import TokenStore
from api.users import UserStore

ADMIN = AuthSettings(username="admin", password="hunter2hunter2")


def basic(username, password):
    return {"Authorization": "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()}


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def make_client(tokens=None):
    tmp = Path(tempfile.mkdtemp())
    users = UserStore(reserved_username=ADMIN.username)
    users.add("op", "operator-pass", "operator")
    users.add("viewer", "viewer-pass", "viewer")
    users.add("ear", "listener-pass", "listener")
    audit = AuditLog()
    app = create_app(
        service=RepeaterService(),
        start_background_tick=False,
        log_path=tmp / "t.log",
        auth_settings=ADMIN,
        users=users,
        tokens=tokens if tokens is not None else TokenStore(),
        audit=audit,
    )
    return TestClient(app), audit


def create(client, name, username):
    response = client.post("/api/tokens", json={"name": name, "username": username}, headers=basic("admin", "hunter2hunter2"))
    assert response.status_code == 200, response.text
    return response.json()["token"]


def test_store_keeps_only_a_digest_and_checks_the_whole_token(tmp_path):
    store = TokenStore(StateStore(tmp_path / "tokens.json"), clock=lambda: 1000.0)
    record, secret = store.create("agent", "op")

    assert secret.startswith("mor_") and store.verify(secret).id == record.id
    assert secret not in (tmp_path / "tokens.json").read_text()
    assert store.verify(secret[:-1] + ("A" if secret[-1] != "A" else "B")) is None
    assert store.verify(f"mor_{record.id}_") is None
    assert store.verify("not-a-token") is None
    assert [t.id for t in TokenStore(StateStore(tmp_path / "tokens.json")).list()] == [record.id]


def test_last_used_is_recorded_without_saving_on_every_request(tmp_path):
    now = {"t": 1000.0}
    store = TokenStore(StateStore(tmp_path / "tokens.json"), clock=lambda: now["t"])
    _, secret = store.create("agent", "op")
    now["t"] = 1010.0
    assert store.verify(secret).last_used_at == 1010.0
    assert json.loads((tmp_path / "tokens.json").read_text())["tokens"][0]["last_used_at"] == 1010.0  # first use
    now["t"] = 1020.0
    store.verify(secret)
    assert json.loads((tmp_path / "tokens.json").read_text())["tokens"][0]["last_used_at"] == 1010.0
    now["t"] = 2000.0
    store.verify(secret)
    assert json.loads((tmp_path / "tokens.json").read_text())["tokens"][0]["last_used_at"] == 2000.0


def test_a_token_acts_with_its_users_role():
    client, _ = make_client()
    viewer = create(client, "dashboard bot", "viewer")
    operator = create(client, "agent", "op")

    assert client.get("/api/status", headers=bearer(viewer)).status_code == 200
    assert client.put("/api/config", json={"hang_time": 2.0}, headers=bearer(viewer)).status_code == 403
    assert client.put("/api/config", json={"hang_time": 2.0}, headers=bearer(operator)).status_code == 200
    assert client.get("/api/status", headers=bearer("mor_nope_nope")).status_code == 401


def test_changes_made_with_a_token_are_audited_with_its_name():
    client, audit = make_client()
    token = create(client, "agent", "op")
    client.put("/api/config", json={"hang_time": 2.0}, headers=bearer(token))
    assert audit.recent(1)[0].actor == "op (token agent)"


def test_revoking_deleting_the_user_or_changing_the_role_applies_immediately():
    client, _ = make_client()
    admin = basic("admin", "hunter2hunter2")
    token = create(client, "agent", "op")

    client.put("/api/users/op", json={"role": "viewer"}, headers=admin)
    assert client.put("/api/config", json={"hang_time": 2.0}, headers=bearer(token)).status_code == 403

    token_id = client.get("/api/tokens", headers=admin).json()[0]["id"]
    assert client.delete(f"/api/tokens/{token_id}", headers=admin).json() == []
    assert client.get("/api/status", headers=bearer(token)).status_code == 401

    token = create(client, "agent 2", "viewer")
    client.delete("/api/users/viewer", headers=admin)
    assert client.get("/api/status", headers=bearer(token)).status_code == 401
    assert client.get("/api/tokens", headers=admin).json() == []


def test_tokens_cant_manage_accounts_even_for_an_admin():
    client, _ = make_client()
    token = create(client, "admin agent", "admin")

    assert client.get("/api/audit", headers=bearer(token)).status_code == 200
    assert client.get("/api/users", headers=bearer(token)).status_code == 403
    assert client.post("/api/tokens", json={"name": "x", "username": "admin"}, headers=bearer(token)).status_code == 403
    assert client.post(
        "/api/users", json={"username": "evil", "password": "evil-pass", "role": "admin"}, headers=bearer(token)
    ).status_code == 403


def test_only_admins_create_tokens_and_not_for_listeners_or_strangers():
    client, _ = make_client()
    assert client.post("/api/tokens", json={"name": "x", "username": "op"}, headers=basic("op", "operator-pass")).status_code == 403
    admin = basic("admin", "hunter2hunter2")
    assert client.post("/api/tokens", json={"name": "x", "username": "ear"}, headers=admin).status_code == 400
    assert client.post("/api/tokens", json={"name": "x", "username": "nobody"}, headers=admin).status_code == 400
    listed = client.get("/api/tokens", headers=admin).json()
    assert listed == []


def test_the_status_websocket_takes_a_token():
    client, _ = make_client()
    token = create(client, "agent", "viewer")
    with client.websocket_connect("/ws/status", headers=bearer(token)) as websocket:
        assert websocket.receive_json()["state"] == "idle"
