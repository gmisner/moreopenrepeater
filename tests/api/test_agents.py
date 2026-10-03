import base64
import json
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.agents import AgentAccess
from api.app import create_app
from api.audit import AuditLog
from api.auth import AuthSettings
from api.persistence import StateStore
from api.service import RepeaterService
from api.tokens import TokenStore
from api.users import UserStore

ADMIN = {"Authorization": "Basic " + base64.b64encode(b"admin:adminadmin").decode()}
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


@pytest.fixture
def repeater():
    tmp = Path(tempfile.mkdtemp())
    users = UserStore(reserved_username="admin")
    users.add("op", "operator-pass", "operator")
    users.add("viewer", "viewer-pass", "viewer")
    tokens = TokenStore()
    _, operator = tokens.create("agent", "op")
    _, viewer = tokens.create("watcher", "viewer")
    audit = AuditLog()
    service = RepeaterService()
    app = create_app(
        service=service,
        start_background_tick=False,
        log_path=tmp / "t.log",
        auth_settings=AuthSettings("admin", "adminadmin"),
        users=users,
        tokens=tokens,
        agent_access=AgentAccess(),
        audit=audit,
    )
    with TestClient(app) as client:
        yield client, {"operator": operator, "viewer": viewer}, audit, service


def call(client, token, method, params=None):
    response = client.post(
        "/mcp",
        headers={**MCP_HEADERS, "Authorization": f"Bearer {token}"},
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
    )
    assert response.status_code == 200, response.text
    return response.json()["result"]


def tool(client, token, name, **arguments):
    result = call(client, token, "tools/call", {"name": name, "arguments": arguments})
    text = result["content"][0]["text"]
    return result["isError"], (text if result["isError"] else json.loads(text))


def test_agents_see_the_repeater_through_its_tools(repeater):
    client, tokens, _, _ = repeater
    init = call(client, tokens["viewer"], "initialize", {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"},
    })
    assert "repeater" in init["instructions"]
    names = {t["name"] for t in call(client, tokens["viewer"], "tools/list")["tools"]}
    assert {"get_status", "get_audio", "get_settings", "update_settings", "recent_logs", "send_id", "dial_dtmf"} <= names

    error, audio = tool(client, tokens["viewer"], "get_audio")
    assert not error and audio["rx_level_db"] == -120.0
    error, status = tool(client, tokens["viewer"], "get_status")
    assert not error and status["state"] == "idle"


def test_settings_changes_follow_the_tokens_role_and_are_audited(repeater):
    client, tokens, audit, service = repeater

    error, message = tool(client, tokens["viewer"], "update_settings", changes={"hang_time": 2.5})
    assert error and "403" in message

    error, settings = tool(client, tokens["operator"], "update_settings", changes={"hang_time": 2.5})
    assert not error and settings["hang_time"] == 2.5 and service.config.hang_time == 2.5
    entry = audit.recent(1)[0]
    assert (entry.actor, entry.action) == ("op (token agent)", "PUT /api/config")

    error, message = tool(client, tokens["operator"], "update_settings", changes={"hang_time": -1})
    assert error and "hang_time" in message


def test_transmitting_needs_an_admin_to_allow_it(repeater):
    client, tokens, _, _ = repeater
    error, message = tool(client, tokens["operator"], "send_id")
    assert error and "can't make the repeater transmit" in message
    assert client.post("/api/simulate/cos", json={"active": True}, headers={"Authorization": f"Bearer {tokens['operator']}"}).status_code == 403
    assert client.post("/api/simulate/cos", json={"active": False}, headers=ADMIN).status_code == 200  # people aren't affected

    assert client.put("/api/agent-access", json={"transmit": True}, headers={"Authorization": f"Bearer {tokens['operator']}"}).status_code == 403
    assert client.put("/api/agent-access", json={"transmit": True}, headers=ADMIN).json()["transmit"] is True

    error, message = tool(client, tokens["operator"], "send_id")
    assert error and "Turn on live audio first" in message  # past the gate
    error, status = tool(client, tokens["operator"], "dial_dtmf", digits="1")
    assert not error and "state" in status
    error, message = tool(client, tokens["viewer"], "send_id")
    assert error and "403" in message  # a viewer still can't


def test_dial_dtmf_checks_the_digits(repeater):
    client, tokens, _, _ = repeater
    client.put("/api/agent-access", json={"transmit": True}, headers=ADMIN)
    error, message = tool(client, tokens["operator"], "dial_dtmf", digits="12; rm")
    assert error and "Digits" in message


def test_the_endpoint_wants_a_token_and_refuses_browsers(repeater):
    client, tokens, _, _ = repeater
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    response = client.post("/mcp", headers=MCP_HEADERS, json=body)
    assert response.status_code == 401 and response.headers["www-authenticate"] == "Bearer"
    assert client.post("/mcp", headers={**MCP_HEADERS, "Authorization": "Bearer mor_bad_bad"}, json=body).status_code == 401
    browser = {**MCP_HEADERS, "Authorization": f"Bearer {tokens['viewer']}", "Origin": "https://evil.example"}
    assert client.post("/mcp", headers=browser, json=body).status_code == 403


def test_agent_access_is_admin_only_and_persists(tmp_path):
    access = AgentAccess(StateStore(tmp_path / "agent-access.json"))
    assert access.transmit is False
    access.set_transmit(True)
    assert AgentAccess(StateStore(tmp_path / "agent-access.json")).transmit is True
