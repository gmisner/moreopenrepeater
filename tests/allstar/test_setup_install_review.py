"""Fixes from a review of the container install's scripts: the layouts that must live in COMPOSE_FILE, a packet radio's interface that
must stay out of the repeater's name, .env's mode, and a wait that must not say "healthy" when it is not (#221)."""
import re
import stat
import subprocess
from pathlib import Path

import pytest

from test_setup import calls_made, run, site, value  # noqa: F401
from test_setup_hardware import NULL, by_id, compose_files, env_file, go, hw, settled, started  # noqa: F401

DOCKER = Path(__file__).resolve().parents[2] / "docker"
DOCS = Path(__file__).resolve().parents[2] / "docs"


# -- --protect-sd and --host-network are kept in COMPOSE_FILE ---------------------------------------------------------------


@pytest.mark.parametrize("flag, off, name", [
    ("--protect-sd", "--no-protect-sd", "compose.protect-sd.yaml"),
    ("--host-network", "--no-host-network", "compose.host-network.yaml"),
])
class TestAnOverlay:
    def test_it_is_added_to_compose_file_and_the_stack_is_restarted(self, hw, flag, off, name):
        result = go(hw, flag)
        assert result.returncode == 0, result.stderr
        assert compose_files(hw) == f"compose.yaml:{name}"
        assert started(hw) and f"turned on {name}" in result.stdout

    def test_it_never_drops_the_hardware_files_and_they_never_drop_it(self, hw, flag, off, name):
        go(hw, "--gpio")
        go(hw, "--serial", by_id(hw))
        assert go(hw, flag).returncode == 0
        assert compose_files(hw) == f"compose.yaml:compose.gpio.yaml:compose.serial.yaml:{name}"
        go(hw, "--gpio")  # a hardware flag run later
        assert compose_files(hw) == f"compose.yaml:compose.gpio.yaml:compose.serial.yaml:{name}"

    def test_it_stays_on_through_a_plain_run_which_is_what_a_rerun_of_setup_does(self, hw, flag, off, name):
        go(hw, flag)
        assert go(hw).returncode == 0
        assert compose_files(hw) == f"compose.yaml:{name}"

    def test_running_it_twice_changes_nothing(self, hw, flag, off, name):
        go(hw, flag)
        first = env_file(hw)
        result = go(hw, flag)
        assert result.returncode == 0 and env_file(hw) == first and f"{name} is already on" in result.stdout
        assert env_file(hw).count(name) == 1

    def test_off_takes_only_that_file_out_and_keeps_the_rest_in_order(self, hw, flag, off, name):
        go(hw, "--gpio")
        go(hw, flag)
        go(hw, "--serial", by_id(hw))
        assert compose_files(hw) == f"compose.yaml:compose.gpio.yaml:{name}:compose.serial.yaml"
        result = go(hw, off)
        assert result.returncode == 0 and f"turned off {name}" in result.stdout
        assert compose_files(hw) == "compose.yaml:compose.gpio.yaml:compose.serial.yaml"
        assert started(hw)

    def test_off_when_it_was_the_only_one_leaves_an_install_that_never_used_one(self, hw, flag, off, name):
        before = settled(hw)
        go(hw, flag)
        go(hw, off)
        assert env_file(hw) == before and "COMPOSE_FILE" not in env_file(hw)

    def test_off_when_it_was_never_on_changes_nothing_and_says_nothing(self, hw, flag, off, name):
        before = settled(hw)
        result = go(hw, off)
        assert result.returncode == 0 and env_file(hw) == before and "turned off" not in result.stdout

    def test_it_follows_the_separator_the_owner_chose(self, hw, flag, off, name):
        (hw["folder"] / ".env").write_text(env_file(hw) + "COMPOSE_PATH_SEPARATOR=;\nCOMPOSE_FILE=compose.yaml;compose.gpio.yaml\n")
        go(hw, flag)
        assert compose_files(hw) == f"compose.yaml;compose.gpio.yaml;{name}"
        go(hw, off)
        assert compose_files(hw) == "compose.yaml;compose.gpio.yaml"

    def test_an_override_file_the_owner_has_is_kept_when_compose_file_is_first_set(self, hw, flag, off, name):
        """With COMPOSE_FILE unset Compose reads compose.override.yaml on its own; once it is set, only what it lists."""
        (hw["folder"] / "compose.override.yaml").write_text("services: {}\n")
        result = go(hw, flag)
        assert compose_files(hw) == f"compose.yaml:compose.override.yaml:{name}"
        assert "compose.override.yaml is kept" in result.stdout

    def test_the_overlay_files_say_to_use_the_flag_and_not_f(self, hw, flag, off, name):
        text = (DOCKER / name).read_text()
        assert f"./setup.sh {flag}" in text and f"./setup.sh {off}" in text and "ignores COMPOSE_FILE" in text
        assert not re.search(r"^#\s+docker compose -f ", text, re.M)  # the line that told people to run it with -f is gone


def test_host_network_says_where_the_loopback_addresses_are_set(hw):
    result = go(hw, "--host-network")
    assert "127.0.0.1" in result.stdout and "core.env" in result.stdout and "docs/allstar.md" in result.stdout
    assert "core.env" not in go(hw, "--protect-sd").stdout


def test_no_doc_or_file_tells_anyone_to_start_compose_with_f_for_these_layouts():
    for path in (DOCS / "docker.md", DOCS / "winlink.md", DOCKER / ".env.example", *DOCKER.glob("compose*.yaml")):
        for line in path.read_text().splitlines():
            if re.search(r"docker compose.* -f compose\.yaml -f compose\.(protect-sd|host-network)\.yaml", line):
                assert "do not" in line.lower() or "Do not" in line or "would" in line, (path, line)
    assert "compose.override.yaml` is not a way" in " ".join((DOCS / "winlink.md").read_text().split()).replace("\n", " ")


def test_the_docs_tell_the_flags():
    text = (DOCS / "docker.md").read_text()
    for flag in ("./setup.sh --protect-sd", "--no-protect-sd", "./setup.sh --host-network", "--no-host-network"):
        assert flag in text, flag
    assert "Compose files and the `-f` option" in text and "ignore `COMPOSE_FILE`" in text.replace("ignores `COMPOSE_FILE`", "ignore `COMPOSE_FILE`")


# -- the packet radio's interface -------------------------------------------------------------------------------------------


def plug_packet_interface(hw):
    (hw["dev"] / "moreopenrepeater-packet").symlink_to(NULL)


def test_packet_radio_installs_a_rule_that_names_and_marks_that_interface(hw):
    plug_packet_interface(hw)
    result = go(hw, "--packet-radio", "D4E5F6")
    assert result.returncode == 0, result.stderr
    rule = (hw["rules"] / "60-moreopenrepeater-packet.rules").read_text()
    assert 'ATTRS{serial}=="D4E5F6"' in rule and 'SYMLINK+="moreopenrepeater-packet"' in rule and 'ENV{MOREOPENREPEATER_PACKET}="1"' in rule
    assert "moreopenrepeater-ptt" not in rule.replace("61-moreopenrepeater-ptt", "")
    ptt_rule = (hw["rules"] / "61-moreopenrepeater-ptt.rules").read_text()
    assert ptt_rule == (hw["folder"] / "udev" / "61-moreopenrepeater-ptt.rules").read_text()
    assert 'ENV{MOREOPENREPEATER_PACKET}=="1", GOTO="moreopenrepeater_ptt_end"' in ptt_rule
    made = " ".join(calls_made(hw["site"]))
    assert "udevadm control --reload-rules" in made and "udevadm trigger --subsystem-match=hidraw --action=add" in made
    assert value(hw["folder"], ".env", "MOR_PACKET_RADIO_ID") == "D4E5F6"
    assert sorted(path.name for path in hw["rules"].iterdir()) == ["60-moreopenrepeater-packet.rules", "61-moreopenrepeater-ptt.rules"]


def test_packet_radio_maps_nothing_into_a_container_and_changes_no_compose_file(hw):
    plug_packet_interface(hw)
    go(hw, "--packet-radio", "D4E5F6")
    text = env_file(hw)
    assert "MOR_PACKET_PTT_HIDRAW" not in text and "COMPOSE_FILE" not in text  # keying with it is the owner's choice (docs/winlink.md)
    assert "MOR_PACKET_PTT_HIDRAW=" in go(hw, "--packet-radio", "D4E5F6").stdout


def test_packet_radio_by_usb_port_for_an_interface_with_no_serial_number(hw):
    plug_packet_interface(hw)
    go(hw, "--packet-radio", "port:1-1.4")
    rule = (hw["rules"] / "60-moreopenrepeater-packet.rules").read_text()
    assert 'KERNELS=="1-1.4"' in rule and "ATTRS{serial}" not in rule


def test_packet_radio_run_twice_installs_once(hw):
    plug_packet_interface(hw)
    go(hw, "--packet-radio", "D4E5F6")
    first = env_file(hw)
    privileged = [line for line in calls_made(hw["site"]) if line.startswith(("sudo", "udevadm"))]
    result = go(hw, "--packet-radio", "D4E5F6")
    assert result.returncode == 0 and "already installed" in result.stdout and env_file(hw) == first
    assert [line for line in calls_made(hw["site"]) if line.startswith(("sudo", "udevadm"))] == privileged


def test_packet_radio_keeps_the_interface_the_owner_chose(hw):
    plug_packet_interface(hw)
    (hw["folder"] / ".env").write_text(env_file(hw) + "MOR_PACKET_RADIO_ID=AAAA\n")
    result = go(hw, "--packet-radio", "BBBB")
    assert "already sets MOR_PACKET_RADIO_ID=AAAA" in result.stdout
    assert 'ATTRS{serial}=="AAAA"' in (hw["rules"] / "60-moreopenrepeater-packet.rules").read_text()


def test_packet_radio_that_is_not_plugged_in_is_refused_and_started_nothing(hw):
    result = go(hw, "--packet-radio", "D4E5F6")
    assert result.returncode == 2 and "moreopenrepeater-packet" in result.stderr and "does not exist" in result.stderr
    assert "./setup.sh --list-interfaces" in result.stderr and not started(hw)


def test_one_interface_cannot_be_both_the_link_radio_and_the_packet_radio(hw):
    (hw["dev"] / "moreopenrepeater-link").symlink_to(NULL)
    plug_packet_interface(hw)
    go(hw, "--link-radio", "D4E5F6")
    result = go(hw, "--packet-radio", "D4E5F6")
    assert result.returncode == 2 and "link radio" in result.stderr
    assert not (hw["rules"] / "60-moreopenrepeater-packet.rules").exists()


def test_packet_radio_and_link_radio_together_leave_the_repeaters_name_to_the_repeater(hw):
    (hw["dev"] / "moreopenrepeater-link").symlink_to(NULL)
    plug_packet_interface(hw)
    assert go(hw, "--link-radio", "AAAA", "--packet-radio", "BBBB").returncode == 0
    assert sorted(path.name for path in hw["rules"].iterdir()) == [
        "60-moreopenrepeater-link.rules", "60-moreopenrepeater-packet.rules", "61-moreopenrepeater-ptt.rules"]
    assert compose_files(hw) == "compose.yaml:compose.link-radio.yaml"


def test_packet_radio_without_an_id_is_a_usage_error(hw):
    result = run(hw["site"], "--callsign", "W1AW", "--packet-radio")
    assert result.returncode == 2 and "--packet-radio needs the interface's serial number" in result.stderr


def test_a_plain_run_installs_no_packet_rule_and_touches_no_udev(hw):
    assert go(hw).returncode == 0
    assert list(hw["rules"].iterdir()) == [] and "MOR_PACKET_RADIO_ID" not in env_file(hw)


def test_the_docs_say_why_and_how_to_keep_the_packet_radio_out_of_the_repeaters_name():
    winlink = " ".join((DOCS / "winlink.md").read_text().split())
    assert "./setup.sh --packet-radio" in winlink and "/dev/moreopenrepeater-packet" in winlink
    assert "key the packet radio instead of the repeater" in winlink and "even if you only use it for sound" in winlink
    docker = " ".join((DOCS / "docker.md").read_text().split())
    assert "./setup.sh --packet-radio" in docker and "any** second CM108 or AIOC" in docker


# -- .env stays owner-only --------------------------------------------------------------------------------------------------


def test_the_env_file_is_owner_only_even_after_setup_rewrote_a_line_in_it(hw):
    """force_value wrote through a new file and mv, which gives the umask's mode (often 644): the docs say readable by you only."""
    go(hw, "--gpio")
    go(hw, "--serial", by_id(hw))  # rewrites COMPOSE_FILE, and a later --localhost rewrites MOR_API_HOST
    go(hw, "--localhost")
    for name in (".env", "api.env", "core.env"):
        assert stat.S_IMODE((hw["folder"] / name).stat().st_mode) == 0o600, name
    assert not list(hw["folder"].glob("*.new"))


def test_a_loose_env_file_is_tightened_and_keeps_its_owner(hw):
    (hw["folder"] / ".env").chmod(0o644)
    before = (hw["folder"] / ".env").stat()
    go(hw, "--localhost")
    after = (hw["folder"] / ".env").stat()
    assert stat.S_IMODE(after.st_mode) == 0o600 and after.st_uid == before.st_uid and after.st_ino == before.st_ino  # same file, written in place


def test_force_value_works_under_a_loose_umask_too(hw):
    result = run(hw["site"], "--callsign", "W1AW", "--localhost", MOR_DEV_DIR=str(hw["dev"]))
    assert result.returncode == 0
    assert stat.S_IMODE((hw["folder"] / ".env").stat().st_mode) == 0o600


# -- the wait --------------------------------------------------------------------------------------------------------------


def unhealthy_docker(hw, status):
    hw["shim"]("docker", f'''echo "docker $*" >> {hw["calls"]}
case "$*" in
  "compose version") echo "Docker Compose version v2.26.1" ;;
  *"ps --format"*) printf '{status}' ;;
esac
exit 0''')


def test_containers_that_never_become_healthy_are_named_and_the_exit_status_says_so(hw):
    unhealthy_docker(hw, "moreopenrepeater-core-1 Up 5 seconds (healthy)\\nmoreopenrepeater-api-1 Up 5 seconds (unhealthy)\\nmoreopenrepeater-logd-1 Restarting (1) 3 seconds ago\\n")
    result = go(hw, MOR_SETUP_WAIT="5")
    assert result.returncode == 1
    assert "not healthy after" in result.stdout and "moreopenrepeater-api-1 Up 5 seconds (unhealthy)" in result.stdout
    assert "moreopenrepeater-logd-1 Restarting" in result.stdout and "docker compose logs" in result.stdout
    after = result.stdout.split("not healthy after")[1]
    assert "moreopenrepeater-core-1" not in after  # the healthy one is not blamed


def test_the_sign_in_is_still_shown_when_something_is_unhealthy_because_it_is_shown_only_once(hw):
    unhealthy_docker(hw, "moreopenrepeater-api-1 Up 5 seconds (health: starting)\\n")
    result = go(hw, MOR_SETUP_WAIT="5")
    password = value(hw["folder"], "api.env", "MOREOPENREPEATER_AUTH_PASSWORD")
    assert result.returncode == 1 and f"admin  /  {password}" in result.stdout and "only time the password is shown" in result.stdout


def test_no_container_at_all_is_not_healthy(hw):
    unhealthy_docker(hw, "")
    result = go(hw, MOR_SETUP_WAIT="5")
    assert result.returncode == 1 and "no container is running" in result.stdout


def test_when_everything_is_healthy_it_exits_zero_as_before(hw):
    result = go(hw)
    assert result.returncode == 0 and "not healthy" not in result.stdout


def test_it_waits_for_a_container_that_becomes_healthy_late(hw):
    flag = hw["folder"].parent / "second-look"
    hw["shim"]("docker", f'''echo "docker $*" >> {hw["calls"]}
case "$*" in
  "compose version") echo "Docker Compose version v2.26.1" ;;
  *"ps --format"*)
    if [ -e {flag} ]; then printf 'moreopenrepeater-api-1 Up 9 seconds (healthy)\\n'
    else touch {flag}; printf 'moreopenrepeater-api-1 Up 1 second (health: starting)\\n'; fi ;;
esac
exit 0''')
    result = go(hw, MOR_SETUP_WAIT="60")
    assert result.returncode == 0 and "not healthy" not in result.stdout


def test_no_start_never_looks_at_health(hw):
    unhealthy_docker(hw, "moreopenrepeater-api-1 Up 5 seconds (unhealthy)\\n")
    result = go(hw, "--no-start")
    assert result.returncode == 0 and "not healthy" not in result.stdout


def test_setup_and_the_scripts_it_runs_are_valid_shell():
    for name in ("setup.sh", "update.sh", "split-winlink-env.sh", "pins/update.sh", "udev/interfaces.sh", "direwolf-entrypoint.sh"):
        assert subprocess.run(["sh", "-n", str(DOCKER / name)]).returncode == 0, name
