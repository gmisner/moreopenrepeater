"""Piper localhost HTTP service lifecycle (model stays loaded across speaks)."""

from __future__ import annotations

import io
import json
from urllib.error import URLError

import pytest

from playout.piper_service import (
    DEFAULT_PIPER_SERVICE_URL,
    PiperService,
    PiperServiceError,
)


class _FakeProc:
    def __init__(self, *, returncode: int | None = None):
        self.returncode = returncode
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = 0

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self, timeout=None):
        if self.returncode is None:
            raise TimeoutError()
        return self.returncode


def test_start_launches_http_server_with_voice_and_data_dir():
    launched: list = []
    proc = _FakeProc()

    def popen(argv, **kwargs):
        launched.append((argv, kwargs))
        return proc

    svc = PiperService(
        python="/opt/venv/bin/python",
        voice="en_GB-cori-high",
        data_dir="/data/piper",
        popen=popen,
    )
    svc.start()

    argv, _kwargs = launched[0]
    assert argv[:3] == ["/opt/venv/bin/python", "-m", "piper.http_server"]
    assert argv[argv.index("--host") + 1] == "127.0.0.1"
    assert argv[argv.index("--port") + 1] == "8766"
    # `-m` also appears as `python -m piper.http_server`; the voice flag is last.
    assert argv[argv.index("--data-dir") - 1] == "en_GB-cori-high"
    assert argv[argv.index("--data-dir") + 1] == "/data/piper"
    assert svc.process is proc
    assert svc.url == DEFAULT_PIPER_SERVICE_URL


def test_start_requires_data_dir():
    svc = PiperService(data_dir="", popen=lambda *a, **k: _FakeProc())
    with pytest.raises(PiperServiceError, match="PIPER_DATA_DIR"):
        svc.start()


def test_wait_ready_polls_info_until_ready():
    calls: list[str] = []
    responses = iter(
        [
            URLError("down"),
            io.BytesIO(json.dumps({"voice": "en_GB-cori-high"}).encode()),
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

    svc = PiperService(
        data_dir="/data/piper",
        urlopen=urlopen,
        sleep=sleep,
        clock=lambda: now["t"],
    )
    svc.process = _FakeProc()
    assert svc.wait_ready(timeout=10) is True
    assert any("/info" in c for c in calls)
    assert now["t"] > 0


def test_wait_ready_fails_when_process_exits():
    svc = PiperService(
        data_dir="/data/piper",
        urlopen=lambda *a, **k: (_ for _ in ()).throw(URLError("x")),
        sleep=lambda _: None,
        clock=lambda: 0.0,
    )
    svc.process = _FakeProc(returncode=1)
    with pytest.raises(PiperServiceError, match="exited"):
        svc.wait_ready(timeout=5)
