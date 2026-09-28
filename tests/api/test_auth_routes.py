import base64
import importlib
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from controller.state_machine import RepeaterConfig

from api.app import create_app
from api.assets import AudioAssetStore
from api.auth import SESSION_COOKIE_NAME, AuthSettings
from api.service import RepeaterService


@pytest.fixture(autouse=True)
def no_login_failure_delay(monkeypatch):
    # `api.app` the attribute is the FastAPI instance, not the module.
    monkeypatch.setattr(importlib.import_module("api.app"), "LOGIN_FAILURE_DELAY_SECONDS", 0)


def make_client(auth_settings=AuthSettings(username="admin", password="hunter2")):
    service = RepeaterService(config=RepeaterConfig(hang_time=1.0, tot_duration=5.0))
    tmp_dir = Path(tempfile.mkdtemp())
    assets_store = AudioAssetStore(tmp_dir / "audio")
    app = create_app(
        service=service,
        start_background_tick=False,
        assets_store=assets_store,
        log_path=tmp_dir / "test.log",
        auth_settings=auth_settings,
    )
    return TestClient(app)


def make_authenticated_client():
    return make_client()


def _auth_header(username: str, password: str) -> dict:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _login(client, username="admin", password="hunter2"):
    return client.post("/api/login", json={"username": username, "password": password})


def test_status_requires_auth_when_configured():
    client = make_authenticated_client()

    response = client.get("/api/status")

    assert response.status_code == 401
    # A WWW-Authenticate: Basic header would make browsers pop their native
    # credentials dialog over the dashboard's own login page.
    assert "www-authenticate" not in response.headers


def test_status_rejects_wrong_credentials():
    client = make_authenticated_client()

    response = client.get("/api/status", headers=_auth_header("admin", "wrong"))

    assert response.status_code == 401


def test_status_accepts_correct_basic_credentials():
    client = make_authenticated_client()

    response = client.get("/api/status", headers=_auth_header("admin", "hunter2"))

    assert response.status_code == 200


def test_index_redirects_to_login_when_signed_out():
    client = make_authenticated_client()

    response = client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_login_page_is_public():
    client = make_authenticated_client()

    response = client.get("/login")

    assert response.status_code == 200
    assert "<form" in response.text


def test_simulate_dtmf_requires_auth_when_configured():
    client = make_authenticated_client()

    response = client.post("/api/simulate/dtmf", json={"digit": "1"})

    assert response.status_code == 401


def test_login_with_correct_credentials_sets_httponly_session_cookie():
    client = make_authenticated_client()

    response = _login(client)

    assert response.status_code == 200
    assert response.json() == {"auth_required": True, "authenticated": True, "username": "admin"}
    set_cookie = response.headers["set-cookie"]
    assert set_cookie.startswith(f"{SESSION_COOKIE_NAME}=")
    assert "HttpOnly" in set_cookie
    assert "samesite=strict" in set_cookie.lower()


def test_login_with_wrong_password_is_rejected_without_cookie():
    client = make_authenticated_client()

    response = _login(client, password="wrong")

    assert response.status_code == 401
    assert "set-cookie" not in response.headers
    assert client.get("/api/status").status_code == 401


def test_session_cookie_grants_access_to_api_and_dashboard():
    client = make_authenticated_client()
    _login(client)

    assert client.get("/api/status").status_code == 200
    assert client.post("/api/simulate/dtmf", json={"digit": "1"}).status_code == 200
    index = client.get("/", follow_redirects=False)
    assert index.status_code == 200
    assert "<html" in index.text


def test_login_page_redirects_home_when_already_signed_in():
    client = make_authenticated_client()
    _login(client)

    response = client.get("/login", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_logout_revokes_the_session_server_side():
    client = make_authenticated_client()
    _login(client)
    token = client.cookies.get(SESSION_COOKIE_NAME)

    client.post("/api/logout")

    assert client.get("/api/status").status_code == 401
    # Replaying the old token must not work either -- the session is gone,
    # not just the cookie.
    client.cookies.set(SESSION_COOKIE_NAME, token)
    assert client.get("/api/status").status_code == 401


def test_session_endpoint_reports_signed_out_then_signed_in():
    client = make_authenticated_client()

    assert client.get("/api/session").json() == {"auth_required": True, "authenticated": False, "username": None}
    _login(client)
    assert client.get("/api/session").json() == {"auth_required": True, "authenticated": True, "username": "admin"}


def test_session_endpoint_reports_auth_not_required_when_disabled():
    client = make_client(auth_settings=None)

    assert client.get("/api/session").json() == {"auth_required": False, "authenticated": True, "username": None}


def test_login_page_redirects_home_when_auth_disabled():
    client = make_client(auth_settings=None)

    response = client.get("/login", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_websocket_rejects_missing_credentials():
    client = make_authenticated_client()

    with pytest.raises(Exception):
        with client.websocket_connect("/ws/status"):
            pass


def test_websocket_accepts_correct_basic_credentials():
    client = make_authenticated_client()

    with client.websocket_connect("/ws/status", headers=_auth_header("admin", "hunter2")) as websocket:
        message = websocket.receive_json()
        assert message["state"] == "idle"


def test_websocket_accepts_session_cookie():
    client = make_authenticated_client()
    _login(client)

    with client.websocket_connect("/ws/status") as websocket:
        assert websocket.receive_json()["state"] == "idle"


def test_websocket_rejects_cross_origin_handshake_even_with_session():
    client = make_authenticated_client()
    _login(client)

    with pytest.raises(Exception):
        with client.websocket_connect("/ws/status", headers={"Origin": "https://evil.example"}):
            pass
