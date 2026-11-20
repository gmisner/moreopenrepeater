"""setup.sh points the API container at the host's Tailscale socket folder, and only when the socket is there."""
import re
import shutil
import socket
import stat
import subprocess
from pathlib import Path

import fake_tailscaled as fake
import pytest

DOCKER = Path(__file__).resolve().parents[2] / "docker"


@pytest.fixture
def site(tmp_path):
    """A copy of the docker folder with a PTT device that exists and shims for what the script calls."""
    folder = tmp_path / "docker"
    shutil.copytree(DOCKER, folder, ignore=shutil.ignore_patterns("__pycache__"))
    (tmp_path / "ptt").write_text("")
    (folder / ".env").write_text(f"MOR_PTT_HIDRAW={tmp_path / 'ptt'}\nMOR_DATA_DIR={tmp_path / 'data'}\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (
        ("docker", 'case "$*" in "compose version") echo "Docker Compose version v2.26.1" ;; esac\nexit 0'),
        ("systemctl", "echo inactive; exit 3"),
        ("hostname", "echo 192.168.1.50"),
    ):
        (bin_dir / name).write_text("#!/bin/sh\n" + body + "\n")
        (bin_dir / name).chmod(0o755)
    return folder, bin_dir


@pytest.fixture
def host_socket():
    """A folder holding a real Unix socket, the way /var/run/tailscale does when tailscaled runs."""
    folder = fake.short_dir()
    listener = socket.socket(socket.AF_UNIX)
    listener.bind(str(folder / "tailscaled.sock"))
    yield folder
    listener.close()


def run(site, socket_dir, *args):
    folder, bin_dir = site
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(folder), "MOR_SETUP_CHOWN": "0", "MOR_TAILSCALE_SOCKET_DIR": str(socket_dir)}
    return subprocess.run(
        ["sh", str(folder / "setup.sh"), "--callsign", "W1AW", "--no-start", *args],
        cwd=folder, env=env, capture_output=True, text=True, timeout=60,
    )


def lines(folder, name="MOR_TAILSCALE_DIR"):
    return re.findall(rf"^{name}=(.*)$", (folder / ".env").read_text(), re.M)


def test_with_the_socket_there_the_folder_is_set_so_compose_mounts_it(site, host_socket):
    result = run(site, host_socket)
    assert result.returncode == 0, result.stderr
    assert lines(site[0]) == [str(host_socket)]


def test_with_no_socket_nothing_is_set_and_the_empty_folder_default_stays(site, tmp_path):
    result = run(site, tmp_path / "no-tailscale-here")
    assert result.returncode == 0, result.stderr
    assert lines(site[0]) == []
    assert "MOR_TAILSCALE_DIR" not in (site[0] / ".env").read_text()


def test_a_folder_without_a_socket_in_it_is_not_enough(site, tmp_path):
    """/var/run/tailscale can exist, empty, when tailscaled is stopped; a plain file is not a socket either."""
    (tmp_path / "ts").mkdir()
    (tmp_path / "ts" / "tailscaled.sock").write_text("")
    assert run(site, tmp_path / "ts").returncode == 0
    assert lines(site[0]) == []


def test_a_value_the_owner_set_is_never_replaced(site, host_socket):
    with open(site[0] / ".env", "a") as env:
        env.write("MOR_TAILSCALE_DIR=/srv/my-tailscale\n")
    assert run(site, host_socket).returncode == 0
    assert lines(site[0]) == ["/srv/my-tailscale"]


def test_running_it_twice_sets_it_once_and_changes_nothing(site, host_socket):
    run(site, host_socket)
    before = (site[0] / ".env").read_bytes()
    assert run(site, host_socket).returncode == 0
    assert (site[0] / ".env").read_bytes() == before
    assert lines(site[0]) == [str(host_socket)]


def test_a_commented_example_does_not_count_as_a_value(site, host_socket):
    with open(site[0] / ".env", "a") as env:
        env.write("# MOR_TAILSCALE_DIR=/var/run/tailscale\n")
    run(site, host_socket)
    assert lines(site[0]) == [str(host_socket)]


def test_the_real_default_is_the_folder_tailscaled_uses():
    text = (DOCKER / "setup.sh").read_text()
    assert '${MOR_TAILSCALE_SOCKET_DIR:-/var/run/tailscale}' in text


def test_the_script_is_still_valid_shell():
    assert subprocess.run(["sh", "-n", str(DOCKER / "setup.sh")]).returncode == 0
    assert (DOCKER / "setup.sh").stat().st_mode & stat.S_IXUSR
