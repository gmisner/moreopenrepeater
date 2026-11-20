"""`setup.sh --import-allstar DIR` records an existing node's folder for the AllStar page's import (docs/allstar.md).

Runs the real script like test_setup.py does, with `docker` and friends stood in by shims in a copy of the folder. It
only records a folder in .env: it never reads, copies or changes what is in it."""
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

DOCKER = Path(__file__).resolve().parents[2] / "docker"


@pytest.fixture
def site(tmp_path):
    folder = tmp_path / "docker"
    shutil.copytree(DOCKER, folder, ignore=shutil.ignore_patterns("__pycache__"))
    (tmp_path / "ptt").write_text("")
    (folder / ".env").write_text(f"MOR_PTT_HIDRAW={tmp_path / 'ptt'}\nMOR_DATA_DIR={tmp_path / 'data'}\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls"

    def shim(name, body):
        path = bin_dir / name
        path.write_text("#!/bin/sh\n" + body)
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    shim("docker", f'''echo "docker $*" >> {calls}
case "$*" in
  "compose version") echo "Docker Compose version v2.26.1" ;;
  *"ps --format"*) printf 'moreopenrepeater-api-1 Up 5 seconds (healthy)\\n' ;;
esac
exit 0''')
    shim("systemctl", "echo inactive; exit 3")
    shim("sudo", f'echo "sudo $*" >> {calls}; exit 0')
    return folder, bin_dir, calls


@pytest.fixture
def host_node(tmp_path):
    """Stands in for the host's /etc/asterisk."""
    node = tmp_path / "etc-asterisk"
    node.mkdir()
    (node / "rpt.conf").write_text("[99001]\n")
    (node / "rpt.conf").chmod(0o644)
    return node


def run(site, *args, **env):
    folder, bin_dir, _ = site
    environment = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(folder), "MOR_SETUP_CHOWN": "0", "MOR_SETUP_WAIT": "5", **env}
    return subprocess.run(["sh", str(folder / "setup.sh"), *args], cwd=folder, env=environment, capture_output=True, text=True, timeout=60)


def value(folder, file, name):
    match = re.search(rf"^{name}=(.*)$", (folder / file).read_text(), re.M)
    return match.group(1) if match else None


def calls_made(site):
    return site[2].read_text().splitlines() if site[2].exists() else []


def test_it_records_the_folder_and_implies_the_allstar_node(site, host_node):
    folder = site[0]
    result = run(site, "--callsign", "W1AW", "--import-allstar", str(host_node))
    assert result.returncode == 0, result.stderr
    assert value(folder, ".env", "MOR_ALLSTAR_IMPORT_DIR") == str(host_node)
    assert value(folder, "allstar.env", "MOR_AMI_SECRET")  # --allstar was implied
    assert "docker compose --profile allstar up -d --build" in calls_made(site)
    assert "import the node" in result.stdout and str(host_node) in result.stdout


def test_a_relative_folder_is_recorded_as_an_absolute_path(site, host_node, tmp_path):
    folder = site[0]
    result = subprocess.run(
        ["sh", str(folder / "setup.sh"), "--callsign", "W1AW", "--import-allstar", "../etc-asterisk", "--no-start"],
        cwd=folder, env={"PATH": f"{site[1]}:/usr/bin:/bin", "HOME": str(folder), "MOR_SETUP_CHOWN": "0"}, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    recorded = Path(value(folder, ".env", "MOR_ALLSTAR_IMPORT_DIR"))
    assert recorded.is_absolute() and ".." not in recorded.parts and recorded.resolve() == host_node.resolve()


def test_running_it_again_changes_nothing(site, host_node):
    folder = site[0]
    run(site, "--callsign", "W1AW", "--import-allstar", str(host_node))
    before = {n: (folder / n).read_bytes() for n in (".env", "api.env", "core.env", "allstar.env")}
    again = run(site, "--import-allstar", str(host_node))
    assert again.returncode == 0
    assert {n: (folder / n).read_bytes() for n in before} == before
    assert (folder / ".env").read_text().count("MOR_ALLSTAR_IMPORT_DIR") == 1


def test_a_folder_you_already_chose_is_never_overwritten(site, host_node, tmp_path):
    folder = site[0]
    chosen = tmp_path / "my-copy"
    chosen.mkdir()
    with open(folder / ".env", "a") as env:
        env.write(f"MOR_ALLSTAR_IMPORT_DIR={chosen}\n")
    result = run(site, "--callsign", "W1AW", "--import-allstar", str(host_node))
    assert result.returncode == 0
    assert value(folder, ".env", "MOR_ALLSTAR_IMPORT_DIR") == str(chosen) and (folder / ".env").read_text().count("MOR_ALLSTAR_IMPORT_DIR") == 1
    assert "left as it is" in result.stdout and str(chosen) in result.stdout


def test_a_missing_folder_or_one_without_rpt_conf_stops_before_starting_anything(site, tmp_path):
    empty = tmp_path / "empty-dir"
    empty.mkdir()
    for target, words in ((tmp_path / "nope", "is not a folder"), (empty, "no rpt.conf")):
        result = run(site, "--callsign", "W1AW", "--import-allstar", str(target))
        assert result.returncode == 2 and words in result.stderr
        assert value(site[0], ".env", "MOR_ALLSTAR_IMPORT_DIR") is None
        assert not [c for c in calls_made(site) if " up" in c]


def test_the_flag_without_a_folder_is_an_error(site):
    result = run(site, "--callsign", "W1AW", "--import-allstar")
    assert result.returncode == 2 and "needs the folder" in result.stderr


@pytest.mark.parametrize("name", ["with space", "dollar$sign", "quo'te"])
def test_a_path_that_would_not_survive_an_env_file_is_refused(site, tmp_path, name):
    odd = tmp_path / name
    odd.mkdir()
    (odd / "rpt.conf").write_text("[99001]\n")
    result = run(site, "--callsign", "W1AW", "--import-allstar", str(odd))
    assert result.returncode == 2 and "survive an .env file" in result.stderr
    assert value(site[0], ".env", "MOR_ALLSTAR_IMPORT_DIR") is None


def test_it_never_changes_the_node_it_was_pointed_at(site, host_node):
    (host_node / "iax.conf").write_text("register => 99001:FAKE-pass-123@register.allstarlink.org\n")
    before = {p.name: (p.read_bytes(), p.stat().st_mode) for p in host_node.iterdir()}
    result = run(site, "--callsign", "W1AW", "--import-allstar", str(host_node))
    assert result.returncode == 0
    assert {p.name: (p.read_bytes(), p.stat().st_mode) for p in host_node.iterdir()} == before
    assert "FAKE-pass-123" not in result.stdout + result.stderr


def test_a_node_only_root_can_read_gets_the_private_copy_recipe(site, host_node):
    (host_node / "rpt.conf").chmod(0o640)
    result = run(site, "--callsign", "W1AW", "--import-allstar", str(host_node))
    assert result.returncode == 0
    assert "not readable by other users" in result.stdout and "chown -R 999:999" in result.stdout and "Delete the copy" in result.stdout


def test_a_readable_node_gets_no_such_note(site, host_node):
    result = run(site, "--callsign", "W1AW", "--import-allstar", str(host_node))
    assert "not readable by other users" not in result.stdout


def test_without_the_flag_nothing_about_an_import_is_written(site):
    folder = site[0]
    result = run(site, "--callsign", "W1AW", "--allstar")
    assert result.returncode == 0
    assert value(folder, ".env", "MOR_ALLSTAR_IMPORT_DIR") is None and "import" not in result.stdout.lower()


def test_help_describes_the_flag(site):
    result = run(site, "--help")
    assert result.returncode == 0 and "--import-allstar DIR" in result.stdout and "--no-start" in result.stdout
