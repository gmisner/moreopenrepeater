"""Kokoro localhost service lifecycle (model stays loaded across speaks)."""

from __future__ import annotations

import io
import json
from urllib.error import URLError

import pytest

from playout.kokoro_service import (
    DEFAULT_KOKORO_SERVICE_URL,
    KokoroService,
    KokoroServiceError,
)


class _FakeProc:
    def __init__(self, *, returncode: int | None = None):
        self.returncode = returncode
        self.terminated = False
        self.killed = False
        self.wait_calls = 0

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = 0

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self, timeout=None):
        self.wait_calls += 1
        if self.returncode is None:
            raise TimeoutError()
        return self.returncode


def test_start_launches_serve_with_home_env():
    launched: list = []
    proc = _FakeProc()

    def popen(argv, **kwargs):
        launched.append((argv, kwargs))
        return proc

    svc = KokoroService(binary="/opt/venv/bin/kokoro", home="/data/kokoro", popen=popen)
    svc.start()

    argv, kwargs = launched[0]
    assert argv == ["/opt/venv/bin/kokoro", "serve"]
    assert kwargs["env"]["KOKORO_HOME"] == "/data/kokoro"
    assert svc.process is proc


def test_wait_ready_polls_health_until_ready():
    calls: list[str] = []
    responses = iter(
        [
            URLError("down"),
            io.BytesIO(json.dumps({"ready": False}).encode()),
            io.BytesIO(json.dumps({"ready": True}).encode()),
        ]
    )
    now = {"t": 0.0}

    def urlopen(req, timeout=0):
        calls.append(getattr(req, "full_url", str(req)))
        item = next(responses)
        if isinstance(item, Exception):
            raise item
        return item

    def sleep(seconds):
        now["t"] += seconds

    svc = KokoroService(
        binary="kokoro",
        urlopen=urlopen,
        sleep=sleep,
        clock=lambda: now["t"],
    )
    svc.process = _FakeProc()
    assert svc.wait_ready(timeout=10) is True
    assert any("/health" in c for c in calls)
    assert now["t"] > 0


def test_wait_ready_fails_when_process_exits():
    svc = KokoroService(binary="kokoro", urlopen=lambda *a, **k: (_ for _ in ()).throw(URLError("x")), sleep=lambda _: None, clock=lambda: 0.0)
    svc.process = _FakeProc(returncode=1)
    with pytest.raises(KokoroServiceError, match="exited"):
        svc.wait_ready(timeout=5)


def test_wait_ready_times_out():
    ticks = {"t": 0.0}

    def clock():
        ticks["t"] += 3.0
        return ticks["t"]

    svc = KokoroService(
        binary="kokoro",
        urlopen=lambda *a, **k: (_ for _ in ()).throw(URLError("x")),
        sleep=lambda _: None,
        clock=clock,
    )
    svc.process = _FakeProc()
    with pytest.raises(KokoroServiceError, match="ready"):
        svc.wait_ready(timeout=5)


def test_stop_terminates_process():
    proc = _FakeProc()
    svc = KokoroService(binary="kokoro")
    svc.process = proc
    svc.stop()
    assert proc.terminated
    assert svc.process is None


def test_default_service_url():
    assert DEFAULT_KOKORO_SERVICE_URL.startswith("http://127.0.0.1:")
