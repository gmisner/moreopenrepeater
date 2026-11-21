"""The Winlink RMS gateway containers (ADR 0006): what the entrypoints will and will not start, and how compose gives them the radio."""
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

DOCKER = Path(__file__).resolve().parents[2] / "docker"
COMPOSE = yaml.safe_load((DOCKER / "compose.yaml").read_text())
SH = shutil.which("sh")

GOOD = {
    "MOR_WINLINK_CALL": "w1aw", "MOR_WINLINK_LOCATOR": "fn31pr", "MOR_WINLINK_ADMIN_PASSWORD": "a-long-admin-password",
    "MOR_WINLINK_KISS_HOST": "172.28.0.40",
}


def run(script, tmp_path, env, *args):
    config = tmp_path / ("bpq32.cfg" if "winlink" in script else "direwolf.conf")
    key = "MOR_WINLINK_CONFIG" if "winlink" in script else "MOR_DIREWOLF_CONFIG"
    render = "MOR_WINLINK_RENDER_ONLY" if "winlink" in script else "MOR_DIREWOLF_RENDER_ONLY"
    result = subprocess.run(
        [SH, str(DOCKER / script), *args], env={"PATH": "/usr/bin:/bin", key: str(config), render: "1", **env},
        capture_output=True, text=True, timeout=20,
    )
    return result, (config.read_text() if config.exists() else None)


def gateway(tmp_path, **env):
    return run("winlink-entrypoint.sh", tmp_path, {**GOOD, **env})


# -- the gateway's configuration ---------------------------------------------------------------------------------------


def test_a_good_setting_renders_a_gateway_that_listens_for_the_modem_and_is_off_winlink_until_it_has_a_password(tmp_path):
    result, cfg = gateway(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "NODECALL=W1AW\n" in cfg and "LOCATOR=FN31PR" in cfg  # case is fixed; the node is the bare call, the gateway is -10
    assert "IPADDR=172.28.0.40" in cfg and "TCPPORT=8001" in cfg and "PROTOCOL=KISS" in cfg
    assert "CMS=" not in cfg and "CMSPASS" not in cfg and "WL2KREPORT" not in cfg  # no way to Winlink, and not listed
    assert "Winlink CMS OFF" in result.stdout  # and it says so, rather than running looking ready
    assert "APPLICATION 1,RMS,C 2 CMS,W1AW-10" in cfg
    assert 'USER=sysop,a-long-admin-password,W1AW,"",SYSOP' in cfg


def test_a_station_connecting_to_the_gateway_call_reaches_the_winlink_application_not_the_node(tmp_path):
    """Found on a bench with a stand-in modem and a stand-in CMS: with the node and the application on the same call a connection
    gets the node (no banner, no Winlink), and with `C 1 CMS` the command goes to the radio port instead of the telnet port that
    holds the CMS link. Neither could ever have served a radio client."""
    _, cfg = gateway(tmp_path, MOR_WINLINK_CMS_PASSWORD="Gw.Pass+1")
    node = re.search(r"^NODECALL=(\S+)$", cfg, re.M).group(1)
    app = re.search(r"^APPLICATION 1,RMS,C (\d+) CMS,(\S+)$", cfg, re.M)
    assert node == "W1AW" and app.group(2) == "W1AW-10" and node != app.group(2)  # two calls: the node and the gateway
    telnet = re.search(r"PORTNUM=(\d+)\n DRIVER=TELNET", cfg).group(1)
    assert app.group(1) == telnet  # the application connects through the telnet port, where CMS=1 is
    assert "CMS=1" in cfg.split("DRIVER=TELNET")[1]


def test_the_gateway_writes_the_two_logs_the_page_reads_and_nothing_more_is_asked_of_it(tmp_path):
    _, cfg = gateway(tmp_path)
    header = cfg.split("\nPORT\n")[0]
    assert "ENABLEADIFLOG=1" in header and "LogAllConnects=1" in header  # the session record and the connect log, in its tmpfs
    assert "LogL4Connects" not in cfg


def test_with_the_winlink_password_it_connects_to_winlink_but_is_still_not_listed(tmp_path):
    result, cfg = gateway(tmp_path, MOR_WINLINK_CMS_PASSWORD="Gw.Pass+1")
    assert result.returncode == 0, result.stderr
    assert "CMS=1" in cfg and "CMSCALL=W1AW" in cfg and "CMSPASS=Gw.Pass+1" in cfg
    assert "WL2KREPORT" not in cfg  # listing the station on Winlink is its own, deliberate setting
    assert "Gw.Pass" not in result.stdout + result.stderr  # the password is never printed


def test_listing_on_winlink_needs_the_password_and_the_frequency(tmp_path):
    bad, _ = gateway(tmp_path, MOR_WINLINK_REPORT="1")
    assert bad.returncode == 2 and "needs MOR_WINLINK_CMS_PASSWORD" in bad.stderr
    bad, _ = gateway(tmp_path, MOR_WINLINK_REPORT="1", MOR_WINLINK_CMS_PASSWORD="pw1")
    assert bad.returncode == 2 and "MOR_WINLINK_FREQ_HZ" in bad.stderr
    ok, cfg = gateway(
        tmp_path, MOR_WINLINK_REPORT="1", MOR_WINLINK_CMS_PASSWORD="pw1", MOR_WINLINK_FREQ_HZ="144390000", MOR_WINLINK_POWER_W="5",
    )
    assert ok.returncode == 0 and "WL2KREPORT PUBLIC, www.winlink.org, 8778, W1AW-10, FN31PR, 00-23, 144390000, PKT1200, 5," in cfg


@pytest.mark.parametrize("name, value, said", [
    ("MOR_WINLINK_CALL", "WQAB123", "GMRS"),          # a GMRS callsign is not an amateur one
    ("MOR_WINLINK_CALL", "KAE1234", "GMRS"),
    ("MOR_WINLINK_CALL", "W1 AW", "not a callsign"),
    ("MOR_WINLINK_CALL", "W1AW\nNODECALL=X", "not a callsign"),
    ("MOR_WINLINK_SERVICE", "gmrs", "not offered on GMRS"),
    ("MOR_WINLINK_SSID", "0", "SSID"),
    ("MOR_WINLINK_SSID", "16", "SSID"),
    ("MOR_WINLINK_LOCATOR", "ZZ99", "grid square"),
    ("MOR_WINLINK_ADMIN_PASSWORD", "short", "12 to 64"),
    ("MOR_WINLINK_ADMIN_PASSWORD", "has space in it 1234", "12 to 64"),
    ("MOR_WINLINK_CMS_PASSWORD", "semi;colon", "cannot hold"),   # ';' would start a comment in the file
    ("MOR_WINLINK_CMS_PASSWORD", "with space", "cannot hold"),
    ("MOR_WINLINK_CMS_PASSWORD", 'quo"te', "cannot hold"),
    ("MOR_WINLINK_KISS_HOST", "a b", "host name"),
    ("MOR_WINLINK_KISS_HOST", "1.2.3.4\nNODECALL=X", "host name"),
    ("MOR_WINLINK_KISS_PORT", "0", "port"),
    ("MOR_WINLINK_KISS_PORT", "70000", "port"),
    ("MOR_WINLINK_POWER_W", "ten", "whole numbers"),
])
def test_a_setting_that_cannot_be_trusted_stops_the_gateway_before_it_starts_and_writes_nothing(tmp_path, name, value, said):
    result, cfg = gateway(tmp_path, **{name: value})
    assert result.returncode == 2 and said in result.stderr, result.stderr
    assert cfg is None  # no file: nothing to start from


@pytest.mark.parametrize("missing", ["MOR_WINLINK_CALL", "MOR_WINLINK_LOCATOR", "MOR_WINLINK_ADMIN_PASSWORD"])
def test_there_are_no_defaults_for_who_the_station_is_or_for_the_admin_login(tmp_path, missing):
    env = {k: v for k, v in GOOD.items() if k != missing}
    result, cfg = run("winlink-entrypoint.sh", tmp_path, env)
    assert result.returncode == 2 and missing in result.stderr and cfg is None


def fake_getent(tmp_path, answer):
    script = tmp_path / "getent"
    script.write_text(f"#!/bin/sh\n{answer}\n")
    script.chmod(0o755)
    return str(script)


def test_a_modem_named_by_host_is_looked_up_because_the_gateway_cannot_and_the_address_is_what_it_is_given(tmp_path):
    getent = fake_getent(tmp_path, "echo '172.28.0.40 STREAM direwolf'")
    result, cfg = gateway(tmp_path, MOR_WINLINK_KISS_HOST="direwolf", MOR_WINLINK_GETENT=getent)
    assert result.returncode == 0, result.stderr
    assert "IPADDR=172.28.0.40" in cfg and "direwolf" not in cfg


def test_a_modem_that_is_not_there_stops_the_gateway_after_waiting_and_writes_nothing(tmp_path):
    getent = fake_getent(tmp_path, "exit 2")
    result, cfg = gateway(
        tmp_path, MOR_WINLINK_KISS_HOST="direwolf", MOR_WINLINK_GETENT=getent, MOR_WINLINK_RESOLVE_TRIES="1",
    )
    assert result.returncode == 2 and "has no address" in result.stderr and cfg is None


def test_the_configuration_with_the_passwords_is_readable_by_its_owner_only(tmp_path):
    _, _ = gateway(tmp_path, MOR_WINLINK_CMS_PASSWORD="pw1")
    assert (tmp_path / "bpq32.cfg").stat().st_mode & 0o077 == 0


# -- the modem ------------------------------------------------------------------------------------------------------------


def modem(tmp_path, **env):
    return run("direwolf-entrypoint.sh", tmp_path, {"MOR_WINLINK_CALL": "W1AW", "MOR_PACKET_CARD": "2", **env})


def test_the_modem_opens_the_one_card_it_was_given_and_nothing_else(tmp_path):
    result, cfg = modem(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "ADEVICE plughw:2,0" in cfg and "MYCALL W1AW-10" in cfg and "MODEM 1200" in cfg
    assert "KISSPORT 8001" in cfg and "AGWPORT 0" in cfg and "PTT" not in cfg


def test_the_modem_needs_a_card_and_never_guesses_one(tmp_path):
    result, cfg = run("direwolf-entrypoint.sh", tmp_path, {"MOR_WINLINK_CALL": "W1AW"})
    assert result.returncode == 2 and "MOR_PACKET_CARD" in result.stderr and cfg is None
    for bad in ("", "1;rm", "all", "100", "-1"):
        result, cfg = modem(tmp_path, MOR_PACKET_CARD=bad)
        assert result.returncode == 2 and cfg is None, bad


def test_the_modem_refuses_a_gmrs_callsign_and_a_ptt_it_does_not_have(tmp_path):
    result, _ = modem(tmp_path, MOR_WINLINK_CALL="WQAB123")
    assert result.returncode == 2 and "GMRS" in result.stderr
    result, _ = modem(tmp_path, MOR_PACKET_PTT="serial")
    assert result.returncode == 2 and "none or cm108" in result.stderr
    result, cfg = modem(tmp_path, MOR_PACKET_PTT="cm108")  # asked for a PTT device that is not in the container
    assert result.returncode == 2 and "no PTT device" in result.stderr and cfg is None


def test_the_test_audio_mode_opens_no_sound_card(tmp_path):
    result, cfg = run("direwolf-entrypoint.sh", tmp_path, {"MOR_WINLINK_CALL": "W1AW", "MOR_DIREWOLF_TEST_AUDIO_UDP": "7355"})
    assert result.returncode == 0 and "ADEVICE UDP:7355 null" in cfg


# -- compose -------------------------------------------------------------------------------------------------------------


def default_of(value):
    match = re.fullmatch(r"\$\{[A-Z_]+:-([^}]*)\}", value)
    assert match, value
    return match.group(1)


@pytest.mark.parametrize("name", ["direwolf", "winlink"])
def test_both_are_off_by_default_locked_down_and_publish_nothing(name):
    service = COMPOSE["services"][name]
    assert service["profiles"] == ["winlink"] and "ports" not in service
    assert list(service["networks"]) == ["services"] and service["user"] == "999:999"
    assert service["read_only"] is True and service["cap_drop"] == ["ALL"] and "no-new-privileges:true" in service["security_opt"]
    assert service["restart"] == "unless-stopped" and service["healthcheck"]["test"][1].endswith("-entrypoint.sh")
    assert "network_mode" not in service  # never the host's network


def test_the_modem_is_given_one_sound_card_never_all_of_dev_snd():
    devices = COMPOSE["services"]["direwolf"]["devices"]
    assert not [d for d in devices if d.split(":")[0].rstrip("/") == "/dev/snd"]
    cards = [d for d in devices if "/dev/snd/" in d]
    assert len(cards) == 3 and all("${MOR_PACKET_CARD:-9}" in d for d in cards)
    assert "moreopenrepeater-ptt" not in " ".join(devices)  # and not the repeater's PTT
    assert "${MOR_PACKET_PTT_HIDRAW:-/dev/null}:/dev/packet-ptt" in devices
    # the default card is one a Pi does not have, so a profile started without setting it fails to start instead of opening card 0 or 1
    assert default_of("${MOR_PACKET_CARD:-9}") == "9"


def test_the_modem_has_a_fixed_address_and_the_gateway_is_told_it():
    modem = COMPOSE["services"]["direwolf"]["networks"]["services"]["ipv4_address"]
    gateway = COMPOSE["services"]["winlink"]["environment"]["MOR_WINLINK_KISS_HOST"].split("#")[0].strip()
    assert modem.split("#")[0].strip() == "${MOR_DIREWOLF_IP:-172.28.0.40}" == gateway.replace("MOR_WINLINK_KISS_HOST: ", "")
    assert default_of(gateway) == "172.28.0.40"  # inside the services subnet, apart from the core, the node and the bridge


def test_the_api_is_told_where_the_gateway_and_the_modem_are_and_still_has_no_docker_socket():
    api = COMPOSE["services"]["api"]
    env = {key: str(value).split("#")[0].strip() for key, value in api["environment"].items()}
    assert env["MOREOPENREPEATER_WINLINK_HOST"] == "winlink" and "winlink" in COMPOSE["services"]
    assert env["MOREOPENREPEATER_WINLINK_MODEM_HOST"] == COMPOSE["services"]["direwolf"]["networks"]["services"]["ipv4_address"].split("#")[0].strip()
    assert "services" in api["networks"]  # the private network the two containers are on
    assert not [v for v in api.get("volumes", []) if "docker.sock" in str(v)]
    assert "winlink" not in str(api.get("depends_on", ""))  # the dashboard never waits for it


def test_the_gateway_has_no_device_and_the_core_is_not_involved():
    gateway = COMPOSE["services"]["winlink"]
    assert "devices" not in gateway and "group_add" not in gateway
    for name in ("core", "api"):
        service = COMPOSE["services"][name]
        assert "winlink" not in str(service.get("depends_on", "")) and "direwolf" not in str(service.get("depends_on", ""))


def test_the_passwords_come_from_an_env_file_that_is_not_in_the_repository():
    for name in ("direwolf", "winlink"):
        files = COMPOSE["services"][name]["env_file"]
        assert files == [{"path": "winlink.env", "required": False}]
    ignore = (Path(__file__).resolve().parents[2] / ".gitignore").read_text()
    assert "/docker/winlink.env" in ignore
    example = (DOCKER / "winlink.env.example").read_text()
    assert not [line for line in example.splitlines() if re.match(r"[A-Z_]+=\S", line)]  # every value is commented out: no default login


# -- the images ----------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("dockerfile, arg", [("Dockerfile.winlink", "LINBPQ_COMMIT"), ("Dockerfile.direwolf", "DIREWOLF_COMMIT")])
def test_each_image_is_built_from_a_pinned_commit_and_checks_it_got_that_commit(dockerfile, arg):
    text = (DOCKER / dockerfile).read_text()
    assert re.search(rf"ARG {arg}=[0-9a-f]{{40}}\n", text)
    assert f'test "$(git rev-parse HEAD)" = "${arg}"' in text
    assert len(re.findall(r"^FROM debian:trixie-slim@sha256:[0-9a-f]{64}", text, re.M)) == 2  # build and final, by digest
    assert "USER 999:999" in text


def test_the_upstream_source_is_not_copied_into_this_repository():
    assert not list(DOCKER.glob("**/linbpq*")) and not list(DOCKER.glob("**/direwolf*.c"))  # nothing of it is shipped from here


def test_what_the_dockerfiles_copy_is_not_hidden_by_dockerignore():
    """The build context is `*` minus a list: a COPY of a file not on the list fails only when someone builds."""
    ignore = (Path(__file__).resolve().parents[2] / ".dockerignore").read_text().splitlines()
    for dockerfile in ("Dockerfile.winlink", "Dockerfile.direwolf"):
        for source in re.findall(r"^COPY (?!--from)(\S+) ", (DOCKER / dockerfile).read_text(), re.M):
            assert f"!{source}" in ignore, f"{dockerfile} copies {source}, which .dockerignore leaves out"


# -- sessions and counters: the reporter inside the containers ---------------------------------------------------------------------


FIXTURES = Path(__file__).parent / "fixtures"
PYTHON_PATH = f"{Path(sys.executable).parent}:/usr/bin:/bin"


def launch(script, tmp_path, env, timeout=30):
    """The real entrypoint, started the way the container starts it, with a stand-in for the program it would exec."""
    config = tmp_path / ("bpq32.cfg" if "winlink" in script else "direwolf.conf")
    key = "MOR_WINLINK_CONFIG" if "winlink" in script else "MOR_DIREWOLF_CONFIG"
    return subprocess.run(
        [SH, str(DOCKER / script)], capture_output=True, text=True, timeout=timeout,
        env={"PATH": PYTHON_PATH, key: str(config), "MOR_WINLINK_REPORTER": str(DOCKER / "winlink-reporter.py"),
             "MOR_WINLINK_SCAN_SECONDS": "0.2", **env},
    )


def test_the_gateway_starts_the_reporter_beside_linbpq_which_reads_the_logs_linbpq_writes(tmp_path):
    fake = tmp_path / "linbpq"
    fake.write_text(
        "#!/bin/sh\nmkdir -p logs\n"
        f"cp {FIXTURES}/bpq_cms_adif_202611.adi logs/BPQ_CMS_ADIF_202611.adi\n"
        f"cp {FIXTURES}/ConnectLog_261121.log logs/ConnectLog_261121.log\n"
        "echo 'Password=NEVER-READ' > logs/Telnet_261121.log\nsleep 1.5\nexit 7\n"
    )
    fake.chmod(0o755)
    status = tmp_path / "status"
    status.mkdir()
    result = launch("winlink-entrypoint.sh", tmp_path, {**GOOD, "MOR_WINLINK_BIN": str(fake), "MOR_WINLINK_STATUS_DIR": str(status)})
    assert result.returncode == 7, result.stderr  # linbpq's own exit code: the restart policy sees what happened
    snap = json.loads((status / "gateway.json").read_text())
    assert snap["counters"]["sessions"] == 2 and snap["counters"]["connects"] == 3
    assert "NEVER-READ" not in (status / "gateway.json").read_text()
    assert not (status / "modem.json").exists()  # the gateway writes only its own file


def test_the_reporter_stops_when_the_program_it_watches_stops(tmp_path):
    """A reporter left running would hold the container's output open after linbpq exits."""
    fake = tmp_path / "linbpq"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(0o755)
    status = tmp_path / "status"
    status.mkdir()
    started = time.monotonic()
    result = launch("winlink-entrypoint.sh", tmp_path, {**GOOD, "MOR_WINLINK_BIN": str(fake), "MOR_WINLINK_STATUS_DIR": str(status),
                                                          "MOR_WINLINK_SCAN_SECONDS": "0.3"}, timeout=20)
    assert result.returncode == 0 and time.monotonic() - started < 10


def test_the_modem_runs_through_the_reporter_with_its_output_intact_and_its_exit_code(tmp_path):
    fake = tmp_path / "direwolf"
    fake.write_text(f"#!/bin/sh\ncat {FIXTURES}/direwolf_stdout.txt\nexit 5\n")
    fake.chmod(0o755)
    status = tmp_path / "status"
    status.mkdir()
    result = launch("direwolf-entrypoint.sh", tmp_path, {
        "MOR_WINLINK_CALL": "W1AW", "MOR_PACKET_CARD": "2", "MOR_DIREWOLF_BIN": str(fake), "MOR_WINLINK_STATUS_DIR": str(status)})
    assert result.returncode == 5, result.stderr
    assert "[0L] W1AW-10>CQ:beacon from the gateway" in result.stdout  # as `docker compose logs direwolf` shows it today
    counters = json.loads((status / "modem.json").read_text())["counters"]
    assert counters["frames_heard"] == 3 and counters["frames_sent"] == 1


@pytest.mark.parametrize("script, env, name", [
    ("winlink-entrypoint.sh", GOOD, "linbpq"),
    ("direwolf-entrypoint.sh", {"MOR_WINLINK_CALL": "W1AW", "MOR_PACKET_CARD": "2"}, "direwolf"),
])
def test_with_no_status_folder_or_no_python_the_program_still_runs_and_says_the_counters_are_off(tmp_path, script, env, name):
    fake = tmp_path / name
    fake.write_text("#!/bin/sh\necho running\nexit 4\n")
    fake.chmod(0o755)
    binary = {"linbpq": "MOR_WINLINK_BIN", "direwolf": "MOR_DIREWOLF_BIN"}[name]
    result = launch(script, tmp_path, {**env, binary: str(fake), "MOR_WINLINK_STATUS_DIR": str(tmp_path / "no-such-folder")})
    assert result.returncode == 4 and "running" in result.stdout and "sessions and counters are off" in result.stdout


# -- compose and the images, for the same ----------------------------------------------------------------------------------


def test_the_snapshots_live_in_memory_and_only_the_api_can_not_write_them():
    volume = COMPOSE["volumes"]["winlink-status"]
    assert volume["driver_opts"]["type"] == "tmpfs" and volume["driver_opts"]["device"] == "tmpfs"  # no SD card writes
    options = dict(part.split("=") for part in volume["driver_opts"]["o"].split(","))
    assert options["uid"] == "999" and options["gid"] == "999" and "size" in options
    mounts = {name: [v for v in COMPOSE["services"][name].get("volumes", []) if str(v).startswith("winlink-status:")]
              for name in COMPOSE["services"]}
    assert mounts["api"] == ["winlink-status:/winlink-status:ro"]  # the API reads and never writes
    assert mounts["direwolf"] == ["winlink-status:/winlink-status"] and mounts["winlink"] == ["winlink-status:/winlink-status"]
    assert all(not found for name, found in mounts.items() if name not in ("api", "direwolf", "winlink"))


def test_each_part_is_told_where_the_folder_is_and_the_api_still_has_no_docker_access():
    def env(name):
        return {k: str(v).split("#")[0].strip() for k, v in COMPOSE["services"][name]["environment"].items()}

    assert env("api")["MOREOPENREPEATER_WINLINK_STATUS_DIR"] == "/winlink-status"
    assert env("direwolf")["MOR_WINLINK_STATUS_DIR"] == "/winlink-status" and env("winlink")["MOR_WINLINK_STATUS_DIR"] == "/winlink-status"
    api = COMPOSE["services"]["api"]
    assert not [v for v in api.get("volumes", []) if "docker.sock" in str(v)] and "privileged" not in api and "pid" not in api


@pytest.mark.parametrize("dockerfile", ["Dockerfile.winlink", "Dockerfile.direwolf"])
def test_each_image_has_python_for_the_reporter_and_the_reporter_in_it(dockerfile):
    text = (DOCKER / dockerfile).read_text()
    assert re.search(r"apt-get install -y --no-install-recommends [^\n]*\bpython3\b", text.replace("\\\n", " "))
    assert "COPY docker/winlink-reporter.py /usr/local/bin/winlink-reporter.py" in text
    assert "PYTHONDONTWRITEBYTECODE=1" in text  # a read-only root


def test_the_reporter_is_standard_library_only():
    source = (DOCKER / "winlink-reporter.py").read_text()
    imports = set(re.findall(r"^(?:import|from) ([a-z_]+)", source, re.M))
    assert imports <= {"argparse", "hashlib", "json", "os", "re", "signal", "subprocess", "sys", "tempfile", "threading", "time",
                       "collections", "datetime", "pathlib", "typing", "__future__"}


def test_the_api_image_has_the_mount_point_for_the_status_folder_so_a_read_only_root_cannot_stop_it():
    text = (DOCKER / "Dockerfile.api").read_text().replace("\\\n", " ")
    assert re.search(r"mkdir -p [^&]*\s/winlink-status\b", text)
