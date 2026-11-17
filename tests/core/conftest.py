import json
import shutil
import tempfile
from pathlib import Path

import pytest

from core.build import build_core


@pytest.fixture(autouse=True)
def no_real_tts(monkeypatch):
    """Never start a text-to-speech server from a test; a developer machine may have one."""
    from api import core_runtime

    monkeypatch.setattr(core_runtime, "kokoro_service_for", lambda renderer: None)
    monkeypatch.setattr(core_runtime, "piper_service_for", lambda renderer, voice="": None)


@pytest.fixture
def sock_dir():
    """Short path: AF_UNIX paths are limited to about 104 characters."""
    path = Path(tempfile.mkdtemp(prefix="mor-", dir="/tmp"))
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def core(tmp_path):
    (tmp_path / "state.json").write_text(json.dumps({"config": {"callsign": "W1AW", "audio_enabled": False}}))
    return build_core({"MOREOPENREPEATER_DATA_DIR": str(tmp_path), "MOREOPENREPEATER_LOG_PATH": str(tmp_path / "c.log")})
