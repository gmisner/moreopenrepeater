"""The dashboard's view of the logd collector's history: search, gaps, the support bundle (#221, #229).

These run a real collector on a real socket, so what is tested is the path the API takes in a container."""
import asyncio
import json
import shutil
import socket
import tempfile
import threading
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.log_history import LogHistory
from api.persistence import StateStore
from api.service import RepeaterService
from api.support_bundle import write_support_bundle
from logd.main import Settings, serve

BOOT = "b0000000000000000000000000000001"


@pytest.fixture
def run_dir():
    path = Path(tempfile.mkdtemp(prefix="mor-", dir="/tmp"))
    yield path
    shutil.rmtree(path, ignore_errors=True)


class Logd:
    """A collector running in its own thread, as it runs in its own container."""

    def __init__(self, run_dir: Path) -> None:
        self.settings = Settings(run_dir / "events.sock", run_dir / "query.sock", run_dir / "data", 8 * 1024 * 1024, 14, 30)
        self.loop = None
        self.stop = None
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        async def main() -> None:
            self.loop = asyncio.get_running_loop()
            self.stop = asyncio.Event()
            await serve(self.settings, self.stop)

        asyncio.run(main())

    def __enter__(self) -> "Logd":
        self.thread.start()
        deadline = time.time() + 10
        while time.time() < deadline and not (self.settings.events_socket.exists() and self.settings.query_socket.exists()):
            time.sleep(0.02)
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        if self.loop and self.stop and not self.stop.is_set():
            self.loop.call_soon_threadsafe(self.stop.set)
        self.thread.join(10)

    def send(self, seq: int, message: str, severity: int = 20, event: str = "test.line", module: str = "core", **context) -> None:
        body = {
            "schema_version": 1, "time": datetime.now(timezone.utc).isoformat(), "boot_id": BOOT, "seq": seq,
            "severity": severity, "event": event, "module": module, "module_version": "0.1.0", "message": message,
            "context": context,
        }
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sender:
            sender.sendto(json.dumps(body).encode(), str(self.settings.events_socket))

    def wait_for_events(self, count: int) -> None:
        history = LogHistory(self.settings.query_socket)
        deadline = time.time() + 10
        while time.time() < deadline:
            if history.stats()["events"] >= count:
                return
            time.sleep(0.05)
        raise AssertionError(f"collector never held {count} events")


def make_client(tmp_path, monkeypatch, query_socket=None) -> TestClient:
    if query_socket is None:
        monkeypatch.delenv("MOREOPENREPEATER_LOGD_QUERY_SOCKET", raising=False)
    else:
        monkeypatch.setenv("MOREOPENREPEATER_LOGD_QUERY_SOCKET", str(query_socket))
    service = RepeaterService(state_store=StateStore(tmp_path / "state.json"))
    return TestClient(create_app(service=service, start_background_tick=False, log_path=tmp_path / "t.log"))


def test_with_no_collector_set_up_the_page_is_told_so_and_searches_refuse(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    status = client.get("/api/log-history/status")
    assert status.status_code == 200 and status.json()["configured"] is False and status.json()["available"] is False
    refused = client.get("/api/log-history/events")
    assert refused.status_code == 503 and refused.json()["code"] == "logd.not_configured"


def test_searching_the_history_filters_and_pages_newest_first(tmp_path, monkeypatch, run_dir):
    with Logd(run_dir) as logd:
        for n in range(6):
            logd.send(n, f"line {n}", severity=40 if n == 3 else 20, event="audio.glitch" if n == 3 else "test.line",
                      module="core" if n % 2 == 0 else "api", sound_card="aioc")
        logd.wait_for_events(6)
        client = make_client(tmp_path, monkeypatch, logd.settings.query_socket)

        status = client.get("/api/log-history/status").json()
        assert status["available"] is True and status["stats"]["events"] == 6 and set(status["stats"]["modules"]) == {"core", "api"}

        everything = client.get("/api/log-history/events?limit=100").json()
        assert [e["message"] for e in everything["events"]] == [f"line {n}" for n in (5, 4, 3, 2, 1, 0)]  # newest first

        errors = client.get("/api/log-history/events?min_severity=error").json()["events"]
        assert [e["message"] for e in errors] == ["line 3"] and errors[0]["context"]["sound_card"] == "aioc"
        assert {e["module"] for e in client.get("/api/log-history/events?module=api").json()["events"]} == {"api"}
        assert [e["event"] for e in client.get("/api/log-history/events?event=audio.*").json()["events"]] == ["audio.glitch"]
        assert [e["message"] for e in client.get("/api/log-history/events?q=line 4").json()["events"]] == ["line 4"]

        first = client.get("/api/log-history/events?limit=4").json()
        assert len(first["events"]) == 4 and first["next_before"] is not None
        second = client.get(f"/api/log-history/events?limit=4&before={first['next_before']}").json()
        assert [e["message"] for e in second["events"]] == ["line 1", "line 0"] and second["next_before"] is None


def test_a_search_logd_refuses_comes_back_as_a_client_error_not_an_outage(tmp_path, monkeypatch, run_dir):
    with Logd(run_dir) as logd:
        client = make_client(tmp_path, monkeypatch, logd.settings.query_socket)
        bad = client.get("/api/log-history/events?since=yesterday-ish")
        assert bad.status_code == 400 and bad.json()["code"] == "logd.refused" and "since" in bad.json()["detail"]
        assert client.get("/api/log-history/events?limit=100000").status_code == 422  # the API's own bound


def test_gaps_the_collector_knows_of_are_shown(tmp_path, monkeypatch, run_dir):
    with Logd(run_dir) as logd:
        logd.send(0, "first")
        logd.send(5, "after a gap")  # 1 to 4 never arrived
        logd.wait_for_events(2)
        client = make_client(tmp_path, monkeypatch, logd.settings.query_socket)
        gaps = client.get("/api/log-history/gaps").json()["gaps"]
        assert [g["kind"] for g in gaps] == ["sequence_gap"] and gaps[0]["count"] == 4 and gaps[0]["module"] == "core"


def test_when_the_collector_goes_away_the_history_says_so_and_the_rest_keeps_working(tmp_path, monkeypatch, run_dir):
    logd = Logd(run_dir)
    logd.__enter__()
    logd.send(0, "hello")
    logd.wait_for_events(1)
    client = make_client(tmp_path, monkeypatch, logd.settings.query_socket)
    assert client.get("/api/log-history/status").json()["available"] is True
    logd.close()

    status = client.get("/api/log-history/status")
    assert status.status_code == 200 and status.json()["available"] is False and status.json()["configured"] is True
    down = client.get("/api/log-history/events")
    assert down.status_code == 503 and down.json()["code"] == "logd.unavailable"
    assert client.get("/api/logs").status_code == 200  # the live log does not depend on it


def test_the_history_needs_a_login(tmp_path, monkeypatch):
    from api.auth import AuthSettings

    monkeypatch.delenv("MOREOPENREPEATER_LOGD_QUERY_SOCKET", raising=False)
    service = RepeaterService(state_store=StateStore(tmp_path / "state.json"))
    client = TestClient(create_app(
        service=service, start_background_tick=False, log_path=tmp_path / "t.log", auth_settings=AuthSettings("owner", "hunter2"),
    ))
    for path in ("status", "events", "gaps"):
        assert client.get(f"/api/log-history/{path}").status_code == 401, path


def _bundle(tmp_path, log_history):
    service = RepeaterService(state_store=StateStore(tmp_path / "state.json"))
    log_path = tmp_path / "t.log"
    log_path.write_text("INFO boot\n")
    path = tmp_path / "bundle.zip"
    write_support_bundle(
        path, service=service, log_path=log_path, repo_dir=tmp_path, update_channel="dev", live_audio_status=None,
        system_readings=None, updater=None, now=1_700_000_000.0, log_history=log_history,
        run=lambda *a, **k: type("R", (), {"returncode": 1, "stdout": "", "stderr": "no"})(),
    )
    with zipfile.ZipFile(path) as archive:
        return json.loads(archive.read("event-history.json")), archive.read("README.txt").decode()


def test_the_support_bundle_carries_warnings_and_gaps_from_the_collector(tmp_path, run_dir):
    with Logd(run_dir) as logd:
        logd.send(0, "routine")
        logd.send(1, "something went wrong", severity=40, event="audio.glitch")
        logd.send(7, "after a gap", severity=30)
        logd.wait_for_events(3)
        history, readme = _bundle(tmp_path, LogHistory(logd.settings.query_socket))
    assert history["available"] is True
    assert [e["message"] for e in history["events"]] == ["after a gap", "something went wrong"]  # warnings and worse only
    assert [g["kind"] for g in history["gaps"]] == ["sequence_gap"] and history["stats"]["events"] == 3
    assert "event-history.json" in readme


def test_the_support_bundle_says_why_when_there_is_no_history(tmp_path, run_dir):
    history, _ = _bundle(tmp_path, None)
    assert history == {"available": False, "reason": "the log collector is not set up on this install"}
    history, _ = _bundle(tmp_path, LogHistory(run_dir / "nothing.sock"))
    assert history["available"] is False and "not reachable" in history["reason"]
