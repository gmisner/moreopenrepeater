"""The container install's setup script (#221, #229): sign-in first, then and only then the network.

The owner's requirement: after install the dashboard answers on localhost, on the LAN address and over Tailscale. The
dashboard is open until an account exists and an open one can key the transmitter, so the script turns sign-in on with a
generated password and only then publishes it on every interface. These run the real script with `docker`, `hostname` and
`tailscale` stood in by small shims, in a copy of the folder."""
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

DOCKER = Path(__file__).resolve().parents[2] / "docker"
LAN = "192.168.1.50"
TAILSCALE = "100.101.102.103"


@pytest.fixture
def site(tmp_path):
    """A copy of the docker folder, a PTT device that exists, and shims for what the script calls."""
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
  *"ps --format"*) printf 'moreopenrepeater-core-1 Up 5 seconds (healthy)\\nmoreopenrepeater-api-1 Up 5 seconds (healthy)\\n' ;;
esac
exit 0''')
    shim("hostname", f'echo "{LAN} 172.17.0.1 fe80::1 {TAILSCALE} fd7a:115c::1"')  # the old way: everything, Docker's bridges too
    shim("ip", f'''cat <<LINES
1: lo    inet 127.0.0.1/8 scope host lo
2: wlan0    inet {LAN}/24 brd 192.168.1.255 scope global dynamic wlan0
3: docker0    inet 172.17.0.1/16 brd 172.17.255.255 scope global docker0
4: br-1a2b3c4d5e6f    inet 172.28.0.1/24 brd 172.28.0.255 scope global br-1a2b3c4d5e6f
5: tailscale0    inet {TAILSCALE}/32 scope global tailscale0
6: veth12ab    inet 10.99.0.1/24 scope global veth12ab
LINES''')
    shim("tailscale", f'[ "$1 $2" = "ip -4" ] && echo {TAILSCALE}; exit 0')
    shim("systemctl", 'echo inactive; exit 3')
    shim("sudo", f'echo "sudo $*" >> {calls}; exit 0')
    return folder, bin_dir, calls


def run(site, *args, tailscale=True, stdin="", path_extra=True, **env):
    folder, bin_dir, calls = site
    path = f"{bin_dir}:/usr/bin:/bin"  # only the shims and the system's tools: a real docker or tailscale on this machine must not leak in
    if not path_extra:  # no `ip` at all: a private bin with just what the script needs
        lean = bin_dir.parent / "lean"
        lean.mkdir(exist_ok=True)
        for tool in ("sh", "sed", "grep", "tr", "head", "cp", "chmod", "mkdir", "stat", "id", "mv", "cat", "printf", "awk", "tail", "dirname", "sleep", "read", "date", "rm", "ls"):
            found = shutil.which(tool, path="/usr/bin:/bin")
            if found and not (lean / tool).exists():
                (lean / tool).symlink_to(found)
        path = f"{bin_dir}:{lean}"
    if not tailscale:
        (bin_dir / "tailscale").unlink(missing_ok=True)
    environment = {"PATH": path, "HOME": str(folder), "MOR_SETUP_CHOWN": "0", "MOR_SETUP_WAIT": "5", "MOR_KMSG_FILE": str(bin_dir.parent / "no-kmsg"), **env}
    return subprocess.run(
        ["sh", str(folder / "setup.sh"), *args], cwd=folder, env=environment, capture_output=True, text=True, timeout=60, input=stdin
    )


def value(folder, file, name):
    match = re.search(rf"^{name}=(.*)$", (folder / file).read_text(), re.M)
    return match.group(1) if match else None


def calls_made(site):
    path = site[2]
    return path.read_text().splitlines() if path.exists() else []


def test_a_first_install_turns_sign_in_on_then_publishes_the_dashboard_and_says_where_to_open_it(site):
    folder = site[0]
    result = run(site, "--callsign", "w1aw")
    assert result.returncode == 0, result.stderr
    password = value(folder, "api.env", "MOREOPENREPEATER_AUTH_PASSWORD")
    assert value(folder, "api.env", "MOREOPENREPEATER_AUTH_USER") == "admin"
    assert password and len(password) >= 20 and re.fullmatch(r"[A-Za-z0-9]+", password)
    assert value(folder, ".env", "MOR_API_HOST") == "0.0.0.0"  # on every interface, because sign-in is on
    out = result.stdout
    assert "http://localhost:8000" in out and f"http://{LAN}:8000" in out and f"http://{TAILSCALE}:8000" in out
    assert f"admin  /  {password}" in out and "only time the password is shown" in out
    assert f"http://{TAILSCALE}:8000" in out and out.count(TAILSCALE) == 1  # Tailscale's address is shown as Tailscale's, not as a LAN one
    assert any(line == "docker compose up -d --build" for line in calls_made(site))


def test_the_callsign_is_seeded_because_the_core_will_not_start_without_one(site):
    folder = site[0]
    run(site, "--callsign", "k1abc/p")
    assert (folder.parent / "data" / "state.json").read_text().strip() == '{"config": {"callsign": "K1ABC/P"}}'


def test_an_existing_saved_configuration_is_left_alone(site):
    folder = site[0]
    (folder.parent / "data").mkdir()
    (folder.parent / "data" / "state.json").write_text('{"config": {"callsign": "N0CALL", "tot_duration": 120}}')
    assert run(site).returncode == 0  # no callsign asked for
    assert '"tot_duration": 120' in (folder.parent / "data" / "state.json").read_text()


def test_the_settings_files_are_readable_by_the_owner_only(site):
    folder = site[0]
    run(site, "--callsign", "W1AW", "--allstar")
    for name in ("api.env", "core.env", "allstar.env"):
        assert stat.S_IMODE((folder / name).stat().st_mode) == 0o600, name


def test_running_it_again_changes_nothing_and_does_not_show_the_password_again(site):
    folder = site[0]
    first = run(site, "--callsign", "W1AW")
    before = {n: (folder / n).read_bytes() for n in (".env", "api.env", "core.env")}
    second = run(site)
    assert second.returncode == 0
    assert {n: (folder / n).read_bytes() for n in before} == before
    password = value(folder, "api.env", "MOREOPENREPEATER_AUTH_PASSWORD")
    assert password in first.stdout and password not in second.stdout and "Sign in as" not in second.stdout


def test_localhost_keeps_the_dashboard_on_the_loopback_and_shows_no_network_addresses(site):
    folder = site[0]
    result = run(site, "--callsign", "W1AW", "--localhost")
    assert value(folder, ".env", "MOR_API_HOST") == "127.0.0.1"
    assert LAN not in result.stdout and TAILSCALE not in result.stdout and "tailscale serve" in result.stdout


def test_an_address_you_already_chose_is_respected(site):
    folder = site[0]
    with open(folder / ".env", "a") as env:
        env.write("MOR_API_HOST=192.168.1.50\n")
    run(site, "--callsign", "W1AW")
    assert value(folder, ".env", "MOR_API_HOST") == "192.168.1.50" and (folder / ".env").read_text().count("MOR_API_HOST") == 1


def test_a_login_you_set_is_kept_and_not_replaced_or_shown(site):
    folder = site[0]
    (folder / "api.env").write_text("MOREOPENREPEATER_AUTH_USER=alice\nMOREOPENREPEATER_AUTH_PASSWORD=my-own-choice-1234\n")
    result = run(site, "--callsign", "W1AW")
    assert value(folder, "api.env", "MOREOPENREPEATER_AUTH_USER") == "alice"
    assert "my-own-choice-1234" not in result.stdout and "Sign in as" not in result.stdout
    assert value(folder, ".env", "MOR_API_HOST") == "0.0.0.0"  # it has a login, so it may be published


@pytest.mark.parametrize("line", ["MOREOPENREPEATER_AUTH_USER=alice", "MOREOPENREPEATER_AUTH_PASSWORD=secret-without-user"])
def test_half_a_login_is_refused_and_nothing_is_published_or_started(site, line):
    """The dashboard refuses to start with only one of them, and a script must not paper over it."""
    folder = site[0]
    (folder / "api.env").write_text(line + "\n")
    result = run(site, "--callsign", "W1AW")
    assert result.returncode == 2 and "only one of" in result.stderr
    assert value(folder, ".env", "MOR_API_HOST") is None
    assert not [c for c in calls_made(site) if "up" in c]


def test_the_dashboard_is_never_published_without_a_login(site):
    """Whatever else happens, MOR_API_HOST=0.0.0.0 appears only together with both login settings."""
    folder = site[0]
    run(site, "--callsign", "W1AW")
    assert value(folder, ".env", "MOR_API_HOST") == "0.0.0.0"
    assert value(folder, "api.env", "MOREOPENREPEATER_AUTH_USER") and value(folder, "api.env", "MOREOPENREPEATER_AUTH_PASSWORD")


@pytest.mark.parametrize("callsign, words", [("", "needs your callsign"), ("hi", "does not look like a callsign"), ("W1AW; rm -rf /", "does not look like a callsign"), ('W1AW"}', "does not look like a callsign")])
def test_a_first_install_without_a_good_callsign_stops_before_starting_anything(site, callsign, words):
    args = ["--callsign", callsign] if callsign else []
    result = run(site, *args)
    assert result.returncode == 2 and words in result.stderr
    assert not (site[0].parent / "data" / "state.json").exists()
    assert not [c for c in calls_made(site) if " up" in c]


def test_the_callsign_is_asked_for_at_a_terminal_only(site):
    """No terminal and no --callsign is an error, not a hang waiting for input."""
    result = run(site, stdin="")
    assert result.returncode == 2 and "needs your callsign" in result.stderr


def test_allstar_gets_a_generated_secret_shared_with_the_api_and_core_and_never_printed(site):
    folder = site[0]
    result = run(site, "--callsign", "W1AW", "--allstar")
    secret = value(folder, "allstar.env", "MOR_AMI_SECRET")
    assert secret and re.fullmatch(r"[a-f0-9]{32}", secret)
    for name in ("api.env", "core.env"):
        assert value(folder, name, "MOREOPENREPEATER_AMI_SECRET") == secret and value(folder, name, "MOREOPENREPEATER_AMI_HOST") == "allstar"
    assert secret not in result.stdout + result.stderr
    assert any(c == "docker compose --profile allstar up -d --build" for c in calls_made(site))


def test_dmr_starts_the_bridge_and_needs_no_secret_from_the_script(site):
    folder = site[0]
    run(site, "--callsign", "W1AW", "--dmr")
    assert not (folder / "allstar.env").exists()  # the network, DMR Id and password are set in the dashboard
    assert any(c == "docker compose --profile dmr up -d --build" for c in calls_made(site))


def test_both_profiles_together(site):
    run(site, "--callsign", "W1AW", "--allstar", "--dmr")
    assert any(c == "docker compose --profile allstar --profile dmr up -d --build" for c in calls_made(site))


def test_without_allstar_there_is_no_node_and_no_node_settings(site):
    folder = site[0]
    run(site, "--callsign", "W1AW")
    assert not (folder / "allstar.env").exists()
    assert value(folder, "api.env", "MOREOPENREPEATER_AMI_HOST") is None
    assert not [c for c in calls_made(site) if "--profile" in c]


def test_a_missing_ptt_device_is_said_plainly_and_nothing_is_started(site):
    folder = site[0]
    (folder / ".env").write_text(f"MOR_PTT_HIDRAW={folder.parent / 'not-there'}\nMOR_DATA_DIR={folder.parent / 'data'}\n")
    result = run(site, "--callsign", "W1AW")
    assert "does not exist" in result.stdout and "./setup.sh --udev" in result.stdout
    assert not [c for c in calls_made(site) if " up" in c]


def test_udev_installs_the_rule_with_sudo(site):
    run(site, "--callsign", "W1AW", "--udev")
    made = calls_made(site)
    assert "sudo cp udev/61-moreopenrepeater-ptt.rules /etc/udev/rules.d/" in made
    assert "sudo udevadm control --reload-rules" in made and "sudo udevadm trigger --subsystem-match=hidraw --action=add" in made


def test_it_will_not_run_beside_the_native_service(site):
    folder, bin_dir, _ = site
    (bin_dir / "systemctl").write_text("#!/bin/sh\necho active\n")
    result = run(site, "--callsign", "W1AW")
    assert result.returncode == 2 and "service is running" in result.stderr
    assert not (folder / "api.env").exists()


def test_no_start_writes_the_settings_and_starts_nothing(site):
    result = run(site, "--callsign", "W1AW", "--no-start")
    assert result.returncode == 0 and not [c for c in calls_made(site) if " up" in c]


def test_tailscale_not_installed_says_so_instead_of_inventing_an_address(site):
    result = run(site, "--callsign", "W1AW", tailscale=False)
    assert "install Tailscale" in result.stdout and TAILSCALE not in result.stdout


def test_an_unknown_option_and_help(site):
    assert run(site, "--bogus").returncode == 2
    helped = run(site, "--help")
    assert helped.returncode == 0 and "--callsign" in helped.stdout and "--localhost" in helped.stdout and "--allstar" in helped.stdout


def test_the_script_is_executable_and_valid_shell():
    assert (DOCKER / "setup.sh").stat().st_mode & stat.S_IXUSR
    assert subprocess.run(["sh", "-n", str(DOCKER / "setup.sh")]).returncode == 0


def test_a_settings_file_you_already_had_with_open_permissions_is_tightened(site):
    """api.env holds the dashboard's password and core.env and allstar.env hold the node's secret."""
    folder = site[0]
    for name in ("api.env", "core.env"):
        (folder / name).write_text("# mine\n")
        (folder / name).chmod(0o644)
    run(site, "--callsign", "W1AW")
    for name in ("api.env", "core.env"):
        assert stat.S_IMODE((folder / name).stat().st_mode) == 0o600, name


def test_docker_bridges_and_tailscale_are_not_listed_as_where_to_open_it_on_your_network(site):
    """Found on a real Pi: `hostname -I` also lists docker0 and the compose network's bridge, which are no use to a person."""
    out = run(site, "--callsign", "W1AW").stdout
    lan_lines = [line for line in out.splitlines() if "on your network" in line]
    assert lan_lines == [f"  on your network:   http://{LAN}:8000"]
    assert "172.17.0.1" not in out and "172.28.0.1" not in out and "10.99.0.1" not in out
    assert f"over Tailscale:    http://{TAILSCALE}:8000" in out  # still found, from Tailscale itself


def test_without_the_ip_command_it_falls_back_to_the_address_list_and_still_leaves_out_ipv6_and_tailscale(site):
    (site[1] / "ip").unlink()
    result = run(site, "--callsign", "W1AW", path_extra=False)
    assert f"on your network:   http://{LAN}:8000" in result.stdout
    assert "fe80" not in result.stdout and f"on your network:   http://{TAILSCALE}" not in result.stdout


# --- a Pi 3 Model B+: USB at full speed (--usb-speed) ----------------------------------------------------------------

CMDLINE = "console=serial0,115200 console=tty1 root=PARTUUID=abcd-02 rootfstype=ext4 fsck.repair=yes rootwait"


@pytest.fixture
def pi(site, tmp_path):
    """A Pi 3 Model B+ as far as the script can tell, with a boot command line it may change and a `sudo` that runs."""
    folder, bin_dir, _calls = site
    (bin_dir / "sudo").write_text('#!/bin/sh\nexec "$@"\n')
    (bin_dir / "sudo").chmod(0o755)
    model = tmp_path / "model"
    model.write_bytes(b"Raspberry Pi 3 Model B Plus Rev 1.3\0")
    cmdline = tmp_path / "cmdline.txt"
    cmdline.write_text(CMDLINE + "\n")
    running = tmp_path / "proc-cmdline"
    running.write_text(CMDLINE + "\n")
    env = {"MOR_PI_MODEL_FILE": str(model), "MOR_CMDLINE_FILE": str(cmdline), "MOR_PROC_CMDLINE": str(running)}
    return site, env, cmdline, running, model


def setup_on(pi, *args):
    site, env, *_ = pi
    return run(site, "--callsign", "W1AW", "--no-start", *args, **env)


def test_a_3b_plus_is_told_about_the_setting_and_nothing_is_changed_unasked(pi):
    _site, _env, cmdline, *_ = pi
    result = setup_on(pi)
    assert result.returncode == 0, result.stderr
    assert "Raspberry Pi 3 Model B+" in result.stdout and "--usb-speed" in result.stdout
    assert "12 Mbit/s" in result.stdout  # the cost is in front of them before they choose
    assert cmdline.read_text() == CMDLINE + "\n"
    assert not (cmdline.parent / "cmdline.txt.before-speed1").exists()


def test_usb_speed_adds_the_setting_to_the_one_line_and_keeps_the_original(pi):
    _site, _env, cmdline, *_ = pi
    result = setup_on(pi, "--usb-speed")
    assert result.returncode == 0, result.stderr
    assert cmdline.read_text() == CMDLINE + " dwc_otg.speed=1\n"
    assert (cmdline.parent / "cmdline.txt.before-speed1").read_text() == CMDLINE + "\n"
    assert "Reboot" in result.stdout and "12 Mbit/s" in result.stdout


def test_usb_speed_run_twice_changes_it_once_and_keeps_the_first_backup(pi):
    _site, _env, cmdline, *_ = pi
    setup_on(pi, "--usb-speed")
    result = setup_on(pi, "--usb-speed")
    assert result.returncode == 0, result.stderr
    assert cmdline.read_text() == CMDLINE + " dwc_otg.speed=1\n"
    assert cmdline.read_text().count("dwc_otg.speed=1") == 1
    assert (cmdline.parent / "cmdline.txt.before-speed1").read_text() == CMDLINE + "\n"
    assert "already has dwc_otg.speed=1" in result.stdout


def test_trailing_spaces_and_a_missing_newline_still_give_one_clean_line(pi):
    _site, _env, cmdline, *_ = pi
    cmdline.write_text(CMDLINE + "   ")
    assert setup_on(pi, "--usb-speed").returncode == 0
    assert cmdline.read_text() == CMDLINE + " dwc_otg.speed=1\n"


def test_nothing_is_done_when_the_running_kernel_already_has_it(pi):
    _site, _env, cmdline, running, _model = pi
    running.write_text(CMDLINE + " dwc_otg.speed=1\n")
    result = setup_on(pi, "--usb-speed")
    assert result.returncode == 0, result.stderr
    assert "already held to full speed" in result.stdout
    assert cmdline.read_text() == CMDLINE + "\n"


def test_a_command_line_that_is_not_one_line_is_left_alone(pi):
    _site, _env, cmdline, *_ = pi
    cmdline.write_text(CMDLINE + "\nsomething=else\n")
    result = setup_on(pi, "--usb-speed")
    assert result.returncode == 2
    assert "single line" in result.stderr
    assert cmdline.read_text() == CMDLINE + "\nsomething=else\n"


def test_usb_speed_is_refused_on_any_other_board_and_nothing_is_mentioned_unasked(pi):
    _site, _env, cmdline, _running, model = pi
    model.write_bytes(b"Raspberry Pi 4 Model B Rev 1.5\0")
    quiet = setup_on(pi)
    assert quiet.returncode == 0 and "Model B+" not in quiet.stdout and "usb-speed" not in quiet.stdout
    result = setup_on(pi, "--usb-speed")
    assert result.returncode == 2
    assert "only for a Raspberry Pi 3 Model B+" in result.stderr and "Raspberry Pi 4 Model B" in result.stderr
    assert cmdline.read_text() == CMDLINE + "\n"


def test_usb_speed_says_so_when_there_is_no_command_line_file_to_change(pi):
    _site, _env, cmdline, *_ = pi
    cmdline.unlink()
    result = setup_on(pi, "--usb-speed")
    assert result.returncode == 2
    assert "can't find the Pi's cmdline.txt" in result.stderr


def test_an_earlier_backup_is_never_overwritten(pi):
    # Set once, taken out again by hand (the original line back), then asked for again: the backup is still the
    # first original, not whatever the file said the second time.
    _site, _env, cmdline, *_ = pi
    backup = cmdline.parent / "cmdline.txt.before-speed1"
    backup.write_text("the first original\n")
    assert setup_on(pi, "--usb-speed").returncode == 0
    assert backup.read_text() == "the first original\n"
    assert cmdline.read_text() == CMDLINE + " dwc_otg.speed=1\n"


# -- the host's kernel log for the core (read-only; docs/docker.md, What the container is allowed to do) -------------------


def fake_kmsg(site, content="6,1,1000,-;usb 1-1: hi\n"):
    path = site[0].parent / "kmsg"
    path.write_text(content)
    return path


def test_a_readable_kernel_log_is_given_to_the_core(site):
    kmsg = fake_kmsg(site)
    result = run(site, "--callsign", "W1AW", MOR_KMSG_FILE=str(kmsg))
    assert result.returncode == 0, result.stderr
    assert value(site[0], ".env", "MOR_KMSG_DEVICE") == str(kmsg)
    assert "read-only" in result.stdout and "MOR_KMSG_DEVICE=/dev/null" in result.stdout  # and how to turn it off


def test_the_real_default_is_dev_kmsg():
    assert 'KMSG="${MOR_KMSG_FILE:-/dev/kmsg}"' in (DOCKER / "setup.sh").read_text()


def test_a_host_without_a_kernel_log_device_is_left_on_the_default(site):
    result = run(site, "--callsign", "W1AW", MOR_KMSG_FILE=str(site[0].parent / "absent"))
    assert result.returncode == 0, result.stderr
    assert value(site[0], ".env", "MOR_KMSG_DEVICE") is None and "kernel log" not in result.stdout


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read anything")
def test_a_kernel_log_the_user_cannot_read_is_not_given_and_the_script_says_so(site):
    kmsg = fake_kmsg(site)
    kmsg.chmod(0)
    result = run(site, "--callsign", "W1AW", MOR_KMSG_FILE=str(kmsg))
    assert result.returncode == 0, result.stderr
    assert value(site[0], ".env", "MOR_KMSG_DEVICE") is None
    assert "is not readable" in result.stdout and "not given the host's kernel log" in result.stdout


def test_a_directory_is_not_a_kernel_log(site):
    folder = site[0].parent / "kmsg-dir"
    folder.mkdir()
    run(site, "--callsign", "W1AW", MOR_KMSG_FILE=str(folder))
    assert value(site[0], ".env", "MOR_KMSG_DEVICE") is None


@pytest.mark.parametrize("chosen", ["/dev/null", "/dev/other"])
def test_a_choice_the_owner_made_is_never_overwritten(site, chosen):
    kmsg = fake_kmsg(site)
    with (site[0] / ".env").open("a") as env:
        env.write(f"MOR_KMSG_DEVICE={chosen}\n")
    result = run(site, "--callsign", "W1AW", MOR_KMSG_FILE=str(kmsg))
    assert result.returncode == 0, result.stderr
    assert (site[0] / ".env").read_text().count("MOR_KMSG_DEVICE") == 1
    assert value(site[0], ".env", "MOR_KMSG_DEVICE") == chosen and "kernel log" not in result.stdout


def test_a_commented_example_line_does_not_count_as_a_choice(site):
    kmsg = fake_kmsg(site)
    with (site[0] / ".env").open("a") as env:
        env.write("# MOR_KMSG_DEVICE=/dev/kmsg\n")
    run(site, "--callsign", "W1AW", MOR_KMSG_FILE=str(kmsg))
    assert value(site[0], ".env", "MOR_KMSG_DEVICE") == str(kmsg)


def test_running_it_twice_sets_it_once_and_says_nothing_the_second_time(site):
    kmsg = fake_kmsg(site)
    first = run(site, "--callsign", "W1AW", MOR_KMSG_FILE=str(kmsg))
    second = run(site, MOR_KMSG_FILE=str(kmsg))
    assert (site[0] / ".env").read_text().count("MOR_KMSG_DEVICE") == 1
    assert "kernel log" in first.stdout and "kernel log" not in second.stdout


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read anything")
def test_an_owner_who_chose_is_not_nagged_about_a_log_they_did_not_want_anyway(site):
    kmsg = fake_kmsg(site)
    kmsg.chmod(0)
    with (site[0] / ".env").open("a") as env:
        env.write("MOR_KMSG_DEVICE=/dev/null\n")
    result = run(site, "--callsign", "W1AW", MOR_KMSG_FILE=str(kmsg))
    assert "kernel log" not in result.stdout and value(site[0], ".env", "MOR_KMSG_DEVICE") == "/dev/null"
