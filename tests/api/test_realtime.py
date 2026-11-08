import asyncio
import json
from contextlib import asynccontextmanager

import httpx
import pytest

from api.app import create_app
from api.auth import AuthSettings, SESSION_COOKIE_NAME, SessionStore
from api.service import RepeaterService
from api.users import UserStore
from controller.state_machine import RepeaterConfig


def make_app(tmp_path, **config):
    service = RepeaterService(config=RepeaterConfig(**config))
    return create_app(
        service=service,
        users=UserStore(),
        start_background_tick=False,
        auth_settings=AuthSettings(username="admin", password="password1"),
        log_path=tmp_path / "test.log",
    ), service


@asynccontextmanager
async def open_socket(app, path, *, token=None, block_frames=False, on_accept=None):
    """Exercise ASGI disconnects and backpressure without a network server."""
    incoming, outgoing = asyncio.Queue(), asyncio.Queue()
    headers = [(b"host", b"testserver")]
    if token:
        headers.append((b"cookie", f"{SESSION_COOKIE_NAME}={token}".encode()))

    async def send(message):
        if on_accept and message["type"] == "websocket.accept":
            on_accept()
        if block_frames and message["type"] == "websocket.send":
            await asyncio.Future()
        await outgoing.put(message)

    async def receive(timeout=1):
        return await asyncio.wait_for(outgoing.get(), timeout=timeout)

    incoming.put_nowait({"type": "websocket.connect"})
    task = asyncio.create_task(app({
        "type": "websocket", "asgi": {"version": "3.0", "spec_version": "2.4"},
        "scheme": "ws", "path": path, "query_string": b"", "headers": headers,
        "client": ("192.0.2.1", 12345), "server": ("testserver", 80),
        "subprotocols": [],
    }, incoming.get, send))
    try:
        assert (await receive())["type"] == "websocket.accept"
        yield receive, incoming, task
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def http_client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


async def login(client, username="admin"):
    response = await client.post("/api/login", json={"username": username, "password": "password1"})
    assert response.status_code == 200
    return response.cookies[SESSION_COOKIE_NAME]


def test_idle_status_socket_unsubscribes_on_disconnect(tmp_path):
    async def run():
        app, service = make_app(tmp_path)
        async with http_client(app) as client:
            token = await login(client)
            async with open_socket(app, "/ws/status", token=token) as (receive, incoming, task):
                assert (await receive())["type"] == "websocket.send"
                assert len(service._subscribers) == 1
                incoming.put_nowait({"type": "websocket.disconnect", "code": 1000})
                await asyncio.wait_for(task, timeout=1)
                assert not service._subscribers

    asyncio.run(run())


def test_status_includes_only_the_latest_snapshot_when_accepting(tmp_path):
    async def run():
        app, service = make_app(tmp_path)
        async with http_client(app) as client:
            token = await login(client)
            async with open_socket(
                app, "/ws/status", token=token, on_accept=lambda: service.simulate_cos(active=True)
            ) as (receive, _incoming, _task):
                assert json.loads((await receive())["text"])["cos_active"] is True
                with pytest.raises(TimeoutError):
                    await receive(timeout=0.02)

    asyncio.run(run())


@pytest.mark.parametrize("path", ["/ws/status", "/ws/audio", "/ws/public/audio"])
def test_logout_closes_idle_websocket(tmp_path, path):
    async def run():
        app, service = make_app(tmp_path, public_page_mode="signed_in")
        async with http_client(app) as client:
            token = await login(client)
            async with open_socket(app, path, token=token) as (receive, _incoming, task):
                assert (await receive())["type"] == "websocket.send"
                assert (await client.post("/api/logout")).status_code == 200
                assert (await receive()) == {"type": "websocket.close", "code": 1008, "reason": ""}
                await asyncio.wait_for(task, timeout=1)
                assert not service._subscribers
                assert (await client.get("/api/public/status")).status_code == 401

    asyncio.run(run())


def test_session_expiry_closes_idle_status_socket(tmp_path, monkeypatch):
    now = 0.0
    monkeypatch.setattr("api.runtime.SessionStore", lambda: SessionStore(60, lambda: now))

    async def run():
        nonlocal now
        app, _service = make_app(tmp_path)
        async with http_client(app) as client:
            token = await login(client)
            async with open_socket(app, "/ws/status", token=token) as (receive, _incoming, _task):
                assert (await receive())["type"] == "websocket.send"
                now = 60
                assert (await receive())["code"] == 1008

    asyncio.run(run())


def test_role_change_closes_idle_status_socket(tmp_path):
    async def run():
        app, _service = make_app(tmp_path)
        async with http_client(app) as admin, http_client(app) as viewer:
            await login(admin)
            assert (await admin.post("/api/users", json={
                "username": "bob", "password": "password1", "role": "viewer",
            })).status_code == 200
            token = await login(viewer, "bob")
            async with open_socket(app, "/ws/status", token=token) as (receive, _incoming, _task):
                assert (await receive())["type"] == "websocket.send"
                assert (await admin.put("/api/users/bob", json={"role": "listener"})).status_code == 200
                assert (await receive())["code"] == 1008

    asyncio.run(run())


def test_restricting_listen_mode_closes_idle_public_socket(tmp_path):
    async def run():
        app, service = make_app(tmp_path, public_page_mode="anyone")
        async with open_socket(app, "/ws/public/audio") as (receive, _incoming, task):
            assert (await receive())["type"] == "websocket.send"
            service.update_config(public_page_mode="signed_in")
            assert (await receive())["code"] == 1008
            await asyncio.wait_for(task, timeout=1)
            service.update_config(public_page_mode="anyone")
            async with http_client(app) as client:
                assert (await client.get("/api/public/status")).json()["listeners"] == 0

    asyncio.run(run())


def test_blocked_status_send_times_out_and_unsubscribes(tmp_path, monkeypatch):
    monkeypatch.setattr("api.routes.realtime.SEND_TIMEOUT_SECONDS", 0.01, raising=False)

    async def run():
        app, service = make_app(tmp_path)
        async with http_client(app) as client:
            token = await login(client)
            async with open_socket(app, "/ws/status", token=token, block_frames=True) as (receive, _incoming, task):
                assert (await receive())["code"] == 1013
                await asyncio.wait_for(task, timeout=1)
                assert not service._subscribers

    asyncio.run(run())
