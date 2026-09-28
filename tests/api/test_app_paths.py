from pathlib import Path

from api.app import _resolve_data_dir, _resolve_log_path


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
