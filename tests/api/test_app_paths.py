import logging
import sys
from pathlib import Path

from api.app import _resolve_data_dir, _resolve_log_path, create_app


def test_startup_messages_reach_the_log_file(tmp_path, monkeypatch):
    def find_cm108(_env):
        logging.getLogger("moreopenrepeater.audio").info("using the CM108 interface at /dev/hidraw3")
        return None

    monkeypatch.setattr(sys.modules["api.app"], "cm108_from_env", find_cm108)
    log_path = tmp_path / "app.log"
    create_app(start_background_tick=False, log_path=log_path)
    assert "using the CM108 interface at /dev/hidraw3" in log_path.read_text()


def test_resolve_data_dir_defaults_to_repo_data_dir_slash_audio():
    repo_data_dir = Path("/repo/data")
    assert _resolve_data_dir({}, repo_data_dir) == Path("/repo/data/audio")


def test_resolve_data_dir_honors_env_override():
    repo_data_dir = Path("/repo/data")
    env = {"MOREOPENREPEATER_DATA_DIR": "/var/lib/moreopenrepeater"}
    assert _resolve_data_dir(env, repo_data_dir) == Path("/var/lib/moreopenrepeater/audio")


def test_resolve_log_path_defaults_to_repo_data_dir():
    repo_data_dir = Path("/repo/data")
    assert _resolve_log_path({}, repo_data_dir) == Path("/repo/data/moreopenrepeater.log")


def test_resolve_log_path_honors_env_override():
    repo_data_dir = Path("/repo/data")
    env = {"MOREOPENREPEATER_LOG_PATH": "/var/log/moreopenrepeater/app.log"}
    assert _resolve_log_path(env, repo_data_dir) == Path("/var/log/moreopenrepeater/app.log")
