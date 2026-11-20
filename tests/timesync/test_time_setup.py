"""`setup.sh --time`: it starts the time container the way --allstar and --dmr start theirs, never changes the host, and warns when the
host already runs its own time sync. The real script, with `docker`, `systemctl` and `sudo` stood in by shims, in a copy of the folder."""
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
    active = tmp_path / "active-units"  # one unit name per line: what `systemctl is-active` says is running
    active.write_text("")

    def shim(name, body):
        path = bin_dir / name
        path.write_text("#!/bin/sh\n" + body)
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    shim("docker", f'''echo "docker $*" >> {calls}
case "$*" in
  "compose version") echo "Docker Compose version v2.26.1" ;;
  *"ps --format"*) printf 'moreopenrepeater-core-1 Up 5 seconds (healthy)\\nmoreopenrepeater-api-1 Up 5 seconds (healthy)\\n' ;;
esac
exit 0''')
    shim("systemctl", f'''echo "systemctl $*" >> {calls}
if [ "$1" = is-active ] && grep -qx "$2" {active}; then echo active; exit 0; fi
echo inactive; exit 3''')
    shim("sudo", f'echo "sudo $*" >> {calls}; exit 0')
    shim("timedatectl", f'echo "timedatectl $*" >> {calls}; exit 0')
    shim("hostname", "echo 192.168.1.50")
    shim("ip", "exit 0")
    shim("tailscale", "exit 0")
    return folder, bin_dir, calls, active


def run(site, *args, **env):
    folder, bin_dir, *_ = site
    return subprocess.run(
        ["sh", str(folder / "setup.sh"), "--callsign", "W1AW", *args], cwd=folder,
        env={"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(folder), "MOR_SETUP_CHOWN": "0", "MOR_SETUP_WAIT": "5", **env},
        capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL,
    )


def calls_made(site):
    path = site[2]
    return path.read_text().splitlines() if path.exists() else []


def host_runs(site, *units):
    site[3].write_text("".join(unit + "\n" for unit in units))


def started(site):
    return [c for c in calls_made(site) if c.startswith("docker compose") and " up" in c]


# -- the flag -------------------------------------------------------------------------------------------------------------------


def test_time_starts_the_time_container_with_its_profile_like_the_other_optional_containers(site):
    result = run(site, "--time")
    assert result.returncode == 0, result.stderr
    assert started(site) == ["docker compose --profile time up -d --build"]


def test_without_time_there_is_no_time_profile_and_no_check_of_the_host(site):
    host_runs(site, "systemd-timesyncd")
    result = run(site)
    assert started(site) == ["docker compose up -d --build"]
    assert "WARNING" not in result.stdout and "time container" not in result.stdout
    assert not [c for c in calls_made(site) if c.startswith("systemctl is-active systemd-timesyncd")]  # not even looked at


def test_it_combines_with_the_other_profiles(site):
    run(site, "--allstar", "--dmr", "--time")
    assert started(site) == ["docker compose --profile allstar --profile dmr --profile time up -d --build"]


def test_it_is_in_the_help(site):
    result = subprocess.run(["sh", str(site[0] / "setup.sh"), "--help"], capture_output=True, text=True)
    assert "--time" in result.stdout and "CAP_SYS_TIME" in result.stdout and "changes nothing" in result.stdout


def test_it_says_in_plain_words_what_privilege_the_container_has_and_how_to_remove_it(site):
    out = run(site, "--time").stdout
    assert "CAP_SYS_TIME" in out and "change this machine's clock" in out
    assert "docker compose --profile time rm -sf chrony" in out and "down" not in out  # never the project-wide down, which the shared project must not get


# -- a time sync on the host --------------------------------------------------------------------------------------------------------


def test_a_host_that_already_syncs_its_clock_gets_a_warning_and_how_to_turn_it_off(site):
    host_runs(site, "systemd-timesyncd")
    result = run(site, "--time")
    out = result.stdout
    assert result.returncode == 0
    assert "WARNING: systemd-timesyncd is running on this machine" in out
    assert "sudo timedatectl set-ntp false" in out and "sudo timedatectl set-ntp true" in out  # how, and how to undo it
    assert "Two programs setting one clock fight each other" in out
    assert "Nothing was changed" in out
    assert started(site) == ["docker compose --profile time up -d --build"]  # asked for, so started anyway


@pytest.mark.parametrize("unit", ["chrony", "chronyd", "ntp", "ntpd", "ntpsec", "openntpd"])
def test_another_time_service_on_the_host_is_named_with_the_command_to_stop_it(site, unit):
    host_runs(site, unit)
    out = run(site, "--time").stdout
    assert f"WARNING: {unit} is running on this machine" in out
    assert f"sudo systemctl disable --now {unit}" in out and f"sudo systemctl enable --now {unit}" in out


def test_every_one_that_is_running_is_listed(site):
    host_runs(site, "systemd-timesyncd", "chrony")
    out = run(site, "--time").stdout
    assert "WARNING: systemd-timesyncd is running" in out and "WARNING: chrony is running" in out
    assert "sudo timedatectl set-ntp false" in out and "sudo systemctl disable --now chrony" in out


def test_with_nothing_else_running_there_is_no_warning(site):
    host_runs(site)
    out = run(site, "--time").stdout
    assert "WARNING" not in out and "fight" not in out


def could_change_the_host(call: str) -> bool:
    """A call the script made that could change the host's time setup. `timedatectl show` only reads (the time zone default
    asks it for the zone); `set-ntp` and the rest would change something, and anything under sudo might."""
    return call.startswith("sudo") or (call.startswith("timedatectl") and not call.startswith("timedatectl show "))


def test_what_counts_as_changing_the_host():
    assert not could_change_the_host("timedatectl show -p Timezone --value")
    assert could_change_the_host("timedatectl set-ntp false")
    assert could_change_the_host("timedatectl")
    assert could_change_the_host("sudo systemctl stop systemd-timesyncd")
    assert not could_change_the_host("systemctl is-active systemd-timesyncd")


def test_the_script_never_changes_the_hosts_time_sync_itself(site):
    host_runs(site, "systemd-timesyncd", "chrony", "ntpd")
    run(site, "--time")
    made = calls_made(site)
    assert not [c for c in made if could_change_the_host(c)]
    assert not [c for c in made if c.startswith("systemctl") and not c.startswith("systemctl is-active")]  # only asks; never stop, disable or enable


def test_the_warning_comes_before_the_container_is_started_and_with_no_start_it_still_warns(site):
    host_runs(site, "systemd-timesyncd")
    out = run(site, "--time", "--no-start").stdout
    assert "WARNING: systemd-timesyncd" in out and started(site) == []


def test_a_unit_that_is_only_installed_or_enabled_but_not_active_is_not_a_warning(site):
    host_runs(site)  # `systemctl is-active` says inactive for everything
    assert "WARNING" not in run(site, "--time").stdout


# -- running it again -------------------------------------------------------------------------------------------------------------------


def test_running_it_twice_changes_nothing_and_starts_the_same_way(site):
    folder = site[0]
    first = run(site, "--time")
    files = {n: (folder / n).read_bytes() for n in sorted(p.name for p in folder.iterdir() if p.is_file())}
    second = run(site, "--time")
    assert first.returncode == 0 and second.returncode == 0
    assert {n: (folder / n).read_bytes() for n in files} == files
    assert sorted(p.name for p in folder.iterdir() if p.is_file()) == sorted(files)
    assert started(site) == ["docker compose --profile time up -d --build"] * 2


def test_it_writes_no_settings_file_of_its_own_and_nothing_into_the_env_files(site):
    folder = site[0]
    before = {p.name for p in folder.iterdir()}
    run(site, "--time")
    new = {p.name for p in folder.iterdir()} - before
    assert new <= {"api.env", "core.env", ".env"} and not any("time" in name for name in new)
    for name in ("api.env", "core.env", ".env"):
        assert "ntp" not in (folder / name).read_text().lower()  # the server is the dashboard's setting, in its own volume


def test_a_value_the_owner_set_is_kept(site):
    folder = site[0]
    with open(folder / ".env", "a") as env:
        env.write("MOR_API_HOST=192.168.1.50\nMOR_API_PORT=9000\n")
    (folder / "api.env").write_text("MOREOPENREPEATER_AUTH_USER=alice\nMOREOPENREPEATER_AUTH_PASSWORD=my-own-choice-1234\n")
    (folder / "api.env").chmod(0o600)
    result = run(site, "--time")
    assert result.returncode == 0
    env_text = (folder / ".env").read_text()
    assert "MOR_API_HOST=192.168.1.50" in env_text and "MOR_API_PORT=9000" in env_text and env_text.count("MOR_API_HOST") == 1
    assert (folder / "api.env").read_text() == "MOREOPENREPEATER_AUTH_USER=alice\nMOREOPENREPEATER_AUTH_PASSWORD=my-own-choice-1234\n"
    assert "my-own-choice-1234" not in result.stdout


def test_the_script_is_still_valid_shell():
    assert subprocess.run(["sh", "-n", str(DOCKER / "setup.sh")]).returncode == 0
    assert subprocess.run(["sh", "-n", str(DOCKER / "update.sh")]).returncode == 0

