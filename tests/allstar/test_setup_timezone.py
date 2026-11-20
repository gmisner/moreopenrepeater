"""The container install's default time zone (#221): setup.sh finds the host's zone and writes it to .env as MOR_TZ, and compose
passes it to the containers as TZ. Without it the containers run on UTC and the repeater says the wrong hour.

These run the real script with `docker`, `timedatectl` and friends stood in by shims, in a copy of the folder (the same
arrangement as test_setup.py)."""
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
import yaml
from test_setup import calls_made, value
from test_setup import site as _site

site = _site  # the fixture: a copy of docker/ with shims

DOCKER = Path(__file__).resolve().parents[2] / "docker"
COMPOSE = yaml.safe_load((DOCKER / "compose.yaml").read_text())["services"]
TOOLS = (
    "sh", "sed", "grep", "tr", "head", "cp", "chmod", "mkdir", "stat", "id", "mv", "cat", "printf", "awk", "tail", "dirname",
    "sleep", "date", "rm", "ls", "readlink", "basename", "tee", "wc", "find",
)


def shim(site, name, body):
    path = site[1] / name
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def timedatectl(site, answer, status=0):
    shim(site, "timedatectl", f'[ "$*" = "show -p Timezone --value" ] || exit 64\n{"echo " + answer if answer else ""}\nexit {status}')


def run(site, *args, has_timedatectl=True, **env):
    """The script, with only the shims and the tools it needs on PATH, so this machine's own timedatectl cannot answer."""
    folder, bin_dir, _calls = site
    lean = bin_dir.parent / "lean"
    lean.mkdir(exist_ok=True)
    for tool in TOOLS:
        found = shutil.which(tool, path="/usr/bin:/bin")
        if found and not (lean / tool).exists():
            (lean / tool).symlink_to(found)
    if not has_timedatectl:
        (bin_dir / "timedatectl").unlink(missing_ok=True)
    environment = {
        "PATH": f"{bin_dir}:{lean}", "HOME": str(folder), "MOR_SETUP_CHOWN": "0", "MOR_SETUP_WAIT": "5",
        # nothing on this machine may answer: point the file sources somewhere that is not there, unless a test gives them
        "MOR_TIMEZONE_FILE": str(folder.parent / "no-such-timezone-file"), "MOR_LOCALTIME_FILE": str(folder.parent / "no-such-localtime"),
        **env,
    }
    return subprocess.run(["sh", str(folder / "setup.sh"), "--callsign", "W1AW"], cwd=folder, env=environment, capture_output=True, text=True, timeout=60)


def zone(folder):
    return value(folder, ".env", "MOR_TZ")


def test_the_zone_timedatectl_reports_becomes_the_default(site):
    timedatectl(site, "Europe/London")
    result = run(site)
    assert result.returncode == 0, result.stderr
    assert zone(site[0]) == "Europe/London"
    assert "time zone: Europe/London" in result.stdout


def test_without_timedatectl_etc_timezone_is_used(site, tmp_path):
    (tmp_path / "timezone").write_text("America/Phoenix\n")
    result = run(site, has_timedatectl=False, MOR_TIMEZONE_FILE=str(tmp_path / "timezone"))
    assert result.returncode == 0, result.stderr
    assert zone(site[0]) == "America/Phoenix"


def test_a_timedatectl_that_knows_nothing_falls_through_to_etc_timezone(site, tmp_path):
    timedatectl(site, "", status=1)
    (tmp_path / "timezone").write_text("Asia/Kolkata\n")
    run(site, MOR_TIMEZONE_FILE=str(tmp_path / "timezone"))
    assert zone(site[0]) == "Asia/Kolkata"


def test_timedatectl_saying_n_a_is_not_a_zone(site, tmp_path):
    timedatectl(site, "n/a")
    (tmp_path / "timezone").write_text("Asia/Tokyo\n")
    run(site, MOR_TIMEZONE_FILE=str(tmp_path / "timezone"))
    assert zone(site[0]) == "Asia/Tokyo"


def test_without_either_the_etc_localtime_link_is_read(site, tmp_path):
    target = tmp_path / "usr" / "share" / "zoneinfo" / "America" / "Denver"
    target.parent.mkdir(parents=True)
    target.write_text("")
    (tmp_path / "localtime").symlink_to(target)
    run(site, has_timedatectl=False, MOR_LOCALTIME_FILE=str(tmp_path / "localtime"))
    assert zone(site[0]) == "America/Denver"


def test_when_the_zone_cannot_be_told_nothing_is_written_and_the_install_goes_on(site):
    result = run(site, has_timedatectl=False)
    assert result.returncode == 0, result.stderr
    assert zone(site[0]) is None and "MOR_TZ" not in (site[0] / ".env").read_text()
    assert "couldn't tell this machine's time zone" in result.stdout and "Date and time" in result.stdout
    assert any(line == "docker compose up -d --build" for line in calls_made(site))  # not stopped by it


@pytest.mark.parametrize("junk", ["Europe/Lon don", "Etc/GMT;rm -rf /", "$(reboot)", "a b"])
def test_something_that_is_not_a_zone_name_never_reaches_the_env_file(site, junk):
    timedatectl(site, f"'{junk}'")
    run(site)
    assert zone(site[0]) is None


def test_a_zone_the_owner_set_is_never_overwritten(site):
    folder = site[0]
    with (folder / ".env").open("a") as env:
        env.write("MOR_TZ=Pacific/Honolulu\n")
    timedatectl(site, "Europe/London")
    run(site)
    assert zone(folder) == "Pacific/Honolulu"
    assert (folder / ".env").read_text().count("MOR_TZ=") == 1


def test_running_it_again_does_not_add_a_second_line_or_change_the_first(site):
    timedatectl(site, "Europe/London")
    run(site)
    before = (site[0] / ".env").read_text()
    timedatectl(site, "Asia/Tokyo")  # the host moved; the install keeps what it had (the dashboard is where it is changed)
    run(site)
    assert (site[0] / ".env").read_text() == before
    assert before.count("MOR_TZ=") == 1


def test_a_commented_example_line_does_not_count_as_set(site):
    with (site[0] / ".env").open("a") as env:
        env.write("# MOR_TZ=America/Phoenix\n")
    timedatectl(site, "Europe/London")
    run(site)
    assert zone(site[0]) == "Europe/London"


# -- compose passes it on ----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("service", ["core", "api"])
def test_compose_gives_the_core_and_the_dashboard_the_zone_with_utc_as_the_fallback(service):
    assert COMPOSE[service]["environment"]["TZ"] == "${MOR_TZ:-UTC}"


def test_the_allstar_node_gets_it_too_since_it_can_say_the_time():
    assert COMPOSE["allstar"]["environment"]["TZ"] == "${MOR_TZ:-UTC}"


@pytest.mark.skipif(shutil.which("docker") is None or subprocess.run(["docker", "compose", "version"], capture_output=True).returncode != 0, reason="needs docker compose")
def test_compose_resolves_it_from_the_env_file(tmp_path):
    folder = tmp_path / "docker"
    shutil.copytree(DOCKER, folder, ignore=shutil.ignore_patterns("__pycache__"))
    (folder / ".env").write_text("MOR_TZ=Asia/Kolkata\n")
    out = subprocess.run(["docker", "compose", "-f", "compose.yaml", "config", "--format", "json"], cwd=folder, capture_output=True, text=True, timeout=60, env={k: v for k, v in os.environ.items() if k != "MOR_TZ"})
    if out.returncode != 0:
        pytest.skip("compose would not render here: " + out.stderr[-200:])
    services = json.loads(out.stdout)["services"]
    assert services["core"]["environment"]["TZ"] == "Asia/Kolkata" and services["api"]["environment"]["TZ"] == "Asia/Kolkata"
    (folder / ".env").write_text("")
    out = subprocess.run(["docker", "compose", "-f", "compose.yaml", "config", "--format", "json"], cwd=folder, capture_output=True, text=True, timeout=60, env={k: v for k, v in os.environ.items() if k != "MOR_TZ"})
    assert json.loads(out.stdout)["services"]["core"]["environment"]["TZ"] == "UTC"
