"""Multi-repeater hub: site registry and httpx-mocked remote polls."""
from __future__ import annotations

import base64
import json
import tempfile
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.auth import AuthSettings
from api.hub import HubPoller, HubSiteStore, normalize_base_url
from api.persistence import StateStore
from api.service import RepeaterService
from api.users import UserStore

ADMIN = AuthSettings(username="admin", password="hunter2hunter2")


def basic(username="admin", password="hunter2hunter2"):
    return {"Authorization": "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()}


def make_client(hub_sites=None, hub_poller=None):
    tmp = Path(tempfile.mkdtemp())
    users = UserStore(reserved_username=ADMIN.username)
    users.add("op", "operator-pass", "operator")
    app = create_app(
        service=RepeaterService(),
        start_background_tick=False,
        log_path=tmp / "t.log",
        auth_settings=ADMIN,
        users=users,
        hub_sites=hub_sites if hub_sites is not None else HubSiteStore(),
        hub_poller=hub_poller,
    )
    return TestClient(app)


def test_normalize_base_url():
    assert normalize_base_url("https://a.example.com/") == "https://a.example.com"
    with pytest.raises(ValueError, match="http"):
        normalize_base_url("ftp://a.example.com")
    with pytest.raises(ValueError, match="origin"):
        normalize_base_url("https://a.example.com/api")


def test_store_persists_and_hides_token_from_api():
    path = Path(tempfile.mkdtemp()) / "hub-sites.json"
    store = HubSiteStore(StateStore(path))
    site = store.add("Production", "https://prod.example.com", "mor_abcd_secret")
    listed = make_client(hub_sites=store).get("/api/hub/sites", headers=basic()).json()
    assert listed == [
        {
            "id": site.id,
            "name": "Production",
            "base_url": "https://prod.example.com",
            "dashboard_url": "https://prod.example.com",
            "token_set": True,
        }
    ]
    assert "api_token" not in listed[0]
    on_disk = json.loads(path.read_text())
    assert on_disk["sites"][0]["api_token"] == "mor_abcd_secret"


def test_crud_and_auth():
    client = make_client()
    assert client.get("/api/hub/sites").status_code == 401
    assert client.get("/api/hub/sites", headers=basic("op", "operator-pass")).status_code == 403

    created = client.post(
        "/api/hub/sites",
        headers=basic(),
        json={"name": "Test", "base_url": "https://test.example.com", "api_token": "mor_tok"},
    )
    assert created.status_code == 200, created.text
    site_id = created.json()[0]["id"]

    updated = client.put(
        f"/api/hub/sites/{site_id}",
        headers=basic(),
        json={"name": "Test Pi"},
    )
    assert updated.json()[0]["name"] == "Test Pi"

    assert client.delete(f"/api/hub/sites/{site_id}", headers=basic()).json() == []


def _mock_transport(routes: dict[str, tuple[int, object]]):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("Authorization") == "Bearer mor_remote_token"
        key = f"{request.method} {request.url.path}"
        query = request.url.query
        if isinstance(query, (bytes, bytearray)):
            query = query.decode()
        if query:
            key += f"?{query}"
        status, body = routes[key]
        return httpx.Response(status, json=body)

    return httpx.MockTransport(handler)


def test_poll_status_with_httpx_mock():
    transport = _mock_transport(
        {
            "GET /api/status": (
                200,
                {
                    "state": "idle",
                    "ptt_active": False,
                    "cos_active": False,
                    "ctcss_hz": None,
                    "linked_nodes": [],
                    "last_clip": None,
                    "timestamp": 1.0,
                    "callsign": "W1AW",
                },
            ),
            "GET /api/updates": (
                200,
                {"available": False, "channel": "stable", "version": {"sha": "abcdef012345", "date": "", "subject": ""}},
            ),
            "GET /api/activity/transmissions?limit=1": (200, [{"started_at": 1_700_000_000.0, "duration": 2.0, "timed_out": False}]),
            "GET /api/alerts": (
                200,
                {
                    "settings": {},
                    "secrets_set": {},
                    "channels": [],
                    "recent": [{"severity": "warning", "title": "Warm Pi"}],
                    "system": {},
                    "last_unexpected_stop": None,
                },
            ),
        }
    )
    store = HubSiteStore()
    site = store.add("Remote", "https://remote.test", "mor_remote_token")
    client = make_client(hub_sites=store, hub_poller=HubPoller(httpx.Client(transport=transport)))

    response = client.get("/api/hub/sites/status", headers=basic())
    assert response.status_code == 200, response.text
    [row] = response.json()
    assert row["id"] == site.id
    assert row["reachable"] is True
    assert row["callsign"] == "W1AW"
    assert row["state"] == "idle"
    assert row["version"] == "abcdef0"
    assert row["channel"] == "stable"
    assert row["last_activity"] == 1_700_000_000.0
    assert row["alerts_summary"] == "warning: Warm Pi"


def test_poll_unreachable_with_httpx_mock():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    store = HubSiteStore()
    site = store.add("Down", "https://down.example.com", "mor_remote_token")
    client = make_client(hub_sites=store, hub_poller=HubPoller(httpx.Client(transport=httpx.MockTransport(handler))))
    [row] = client.get("/api/hub/sites/status", headers=basic()).json()
    assert row["id"] == site.id
    assert row["reachable"] is False
    assert "unreachable" in row["error"]


def test_poll_one_site():
    transport = _mock_transport(
        {
            "GET /api/status": (
                200,
                {
                    "state": "receiving",
                    "ptt_active": True,
                    "cos_active": True,
                    "ctcss_hz": None,
                    "linked_nodes": [],
                    "last_clip": None,
                    "timestamp": 2.0,
                    "callsign": "K1ABC",
                },
            ),
            "GET /api/updates": (403, {"detail": "admin"}),
            "GET /api/activity/transmissions?limit=1": (200, []),
            "GET /api/alerts": (403, {"detail": "admin"}),
        }
    )
    store = HubSiteStore()
    site = store.add("Viewer token site", "https://viewer.example.com", "mor_remote_token")
    client = make_client(hub_sites=store, hub_poller=HubPoller(httpx.Client(transport=transport)))
    row = client.get(f"/api/hub/sites/{site.id}/status", headers=basic()).json()
    assert row["reachable"] is True
    assert row["callsign"] == "K1ABC"
    assert row["version"] is None
    assert row["alerts_summary"] is None
    assert row["last_activity"] is None
