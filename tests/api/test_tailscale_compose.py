"""The API sees the host's Tailscale socket read-only, and a machine without Tailscale starts as before."""
import re
import subprocess
from pathlib import Path

import yaml

from api.tailscale_localapi import DEFAULT_SOCKET

DOCKER = Path(__file__).resolve().parents[2] / "docker"
SERVICES = yaml.safe_load((DOCKER / "compose.yaml").read_text())["services"]
MOUNT = [v for v in SERVICES["api"]["volumes"] if isinstance(v, str) and ":/var/run/tailscale" in v]


def test_the_api_gets_the_socket_folder_and_nothing_else_does():
    assert len(MOUNT) == 1
    others = [n for n, s in SERVICES.items() if n != "api" and any("tailscale" in str(v) for v in s.get("volumes", []))]
    assert others == []


def test_it_is_read_only():
    assert MOUNT[0].endswith(":/var/run/tailscale:ro")


def test_it_mounts_the_folder_not_the_socket_file_because_tailscaled_makes_a_new_socket_when_it_restarts():
    source, target, _mode = MOUNT[0].rsplit(":", 2)
    assert target == "/var/run/tailscale" and not source.endswith(".sock")


def test_the_default_source_is_an_empty_folder_in_the_repo_so_docker_never_creates_one_on_the_host():
    match = re.fullmatch(r"\$\{MOR_TAILSCALE_DIR:-(.+?)\}:/var/run/tailscale:ro", MOUNT[0])
    assert match, MOUNT[0]
    default = (DOCKER / match.group(1)).resolve()  # compose resolves a relative source against the compose file's folder
    assert default == (DOCKER / "empty").resolve() and default.is_dir()
    assert sorted(p.name for p in default.iterdir()) == [".gitkeep"]


def test_the_empty_folder_is_committed_not_ignored():
    root = DOCKER.parent
    ignored = subprocess.run(["git", "check-ignore", "-q", "docker/empty/.gitkeep"], cwd=root, capture_output=True)
    if ignored.returncode == 128:
        return  # not a git checkout (a source archive)
    assert ignored.returncode == 1, "docker/empty/.gitkeep is ignored by git, so the default mount source would be missing"
    # Not ignored means `git add` takes it; the file is in the change that adds this test.
    assert (DOCKER / "empty" / ".gitkeep").is_file()


def test_the_api_looks_for_the_socket_inside_the_folder_it_is_given():
    assert SERVICES["api"]["environment"]["MOREOPENREPEATER_TAILSCALE_SOCKET"] == DEFAULT_SOCKET == "/var/run/tailscale/tailscaled.sock"


def test_the_variable_is_documented_where_the_owner_looks():
    assert "MOR_TAILSCALE_DIR" in (DOCKER / ".env.example").read_text()
