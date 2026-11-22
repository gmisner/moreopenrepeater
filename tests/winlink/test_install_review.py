"""Fixes from a review of the container install's Winlink side: the hardware TNC setting, the modem's own settings file, and a PTT
device that is really /dev/null."""
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

from test_winlink import GOOD, gateway, modem, run

DOCKER = Path(__file__).resolve().parents[2] / "docker"
DOCS = Path(__file__).resolve().parents[2] / "docs"
COMPOSE = yaml.safe_load((DOCKER / "compose.yaml").read_text())
SH = shutil.which("sh")
HAVE_DOCKER = shutil.which("docker") is not None and subprocess.run(["docker", "compose", "version"], capture_output=True).returncode == 0
PASSWORDS = ("MOR_WINLINK_CMS_PASSWORD", "MOR_WINLINK_ADMIN_PASSWORD")


# -- a hardware TNC named in winlink.env --------------------------------------------------------------------------------


def test_compose_does_not_set_the_modem_host_where_it_would_beat_winlink_env():
    """A value under `environment:` wins over `env_file:`, so MOR_WINLINK_KISS_HOST there made the owner's setting in winlink.env do
    nothing. The gateway is given the modem's address under another name, which only fills in the default."""
    environment = COMPOSE["services"]["winlink"]["environment"]
    assert "MOR_WINLINK_KISS_HOST" not in environment and "MOR_WINLINK_KISS_PORT" not in environment
    assert str(environment["MOR_DIREWOLF_IP"]).startswith("${MOR_DIREWOLF_IP:-172.28.0.40}")


def configured(tmp_path, winlink_env="", direwolf_env=""):
    """What Compose would hand each container, with these settings files in a copy of the docker folder."""
    folder = tmp_path / "docker"
    shutil.copytree(DOCKER, folder, ignore=shutil.ignore_patterns("__pycache__"))
    (folder / ".env").write_text("")
    for name, text in (("winlink.env", winlink_env), ("direwolf.env", direwolf_env)):
        (folder / name).write_text(text)
    out = subprocess.run(
        ["docker", "compose", "-f", "compose.yaml", "--profile", "winlink", "config", "--format", "json"], cwd=folder,
        capture_output=True, text=True, timeout=60, env={k: v for k, v in os.environ.items() if not k.startswith(("MOR_", "COMPOSE_"))},
    )
    if out.returncode != 0:
        pytest.skip("compose would not render here: " + out.stderr[-200:])
    return json.loads(out.stdout)["services"]


@pytest.mark.skipif(not HAVE_DOCKER, reason="needs docker compose")
def test_a_hardware_tnc_named_in_winlink_env_reaches_the_gateway_as_the_modem_it_connects_to(tmp_path):
    services = configured(tmp_path, "MOR_WINLINK_KISS_HOST=192.168.1.50\nMOR_WINLINK_KISS_PORT=8002\n")
    env = services["winlink"]["environment"]
    assert env["MOR_WINLINK_KISS_HOST"] == "192.168.1.50" and env["MOR_WINLINK_KISS_PORT"] == "8002"
    result, cfg = run("winlink-entrypoint.sh", tmp_path, {**GOOD, **env, "MOR_WINLINK_KISS_HOST": env["MOR_WINLINK_KISS_HOST"]})
    assert result.returncode == 0, result.stderr
    assert "IPADDR=192.168.1.50" in cfg and "TCPPORT=8002" in cfg


@pytest.mark.skipif(not HAVE_DOCKER, reason="needs docker compose")
def test_without_a_tnc_the_gateway_still_connects_to_the_modem_container_at_its_fixed_address(tmp_path):
    env = configured(tmp_path, "MOR_WINLINK_CALL=W1AW\n")["winlink"]["environment"]
    assert "MOR_WINLINK_KISS_HOST" not in env and env["MOR_DIREWOLF_IP"] == "172.28.0.40"
    plain = {k: v for k, v in GOOD.items() if k != "MOR_WINLINK_KISS_HOST"}
    result, cfg = run("winlink-entrypoint.sh", tmp_path, {**plain, **env})
    assert result.returncode == 0, result.stderr
    assert "IPADDR=172.28.0.40" in cfg  # today's behavior


def test_the_entrypoint_prefers_the_owners_modem_then_the_fixed_address_then_the_name(tmp_path):
    plain = {k: v for k, v in GOOD.items() if k != "MOR_WINLINK_KISS_HOST"}
    _, cfg = run("winlink-entrypoint.sh", tmp_path, {**plain, "MOR_DIREWOLF_IP": "172.28.0.77"})
    assert "IPADDR=172.28.0.77" in cfg
    _, cfg = run("winlink-entrypoint.sh", tmp_path, {**plain, "MOR_DIREWOLF_IP": "172.28.0.77", "MOR_WINLINK_KISS_HOST": "10.0.0.5"})
    assert "IPADDR=10.0.0.5" in cfg  # the owner's setting wins
    _, cfg = run("winlink-entrypoint.sh", tmp_path, {**plain, "MOR_DIREWOLF_IP": "172.28.0.77", "MOR_WINLINK_KISS_HOST": ""})
    assert "IPADDR=172.28.0.77" in cfg  # an empty line in winlink.env is "not set"


# -- the modem is not given the gateway's passwords --------------------------------------------------------------------------


def test_the_modem_service_names_no_password_and_reads_only_its_own_settings_file():
    service = COMPOSE["services"]["direwolf"]
    assert service["env_file"] == [{"path": "direwolf.env", "required": False}]
    assert "winlink.env" not in yaml.safe_dump(service)
    names = [name.upper() for name in service.get("environment", {})]
    assert not [name for name in names if "PASSWORD" in name or "SECRET" in name or "CMS" in name]


def test_the_modem_entrypoint_reads_none_of_the_gateways_passwords():
    text = (DOCKER / "direwolf-entrypoint.sh").read_text()
    for name in PASSWORDS:
        assert name not in text


@pytest.mark.skipif(not HAVE_DOCKER, reason="needs docker compose")
def test_compose_gives_the_modem_no_password_even_when_winlink_env_has_both(tmp_path):
    services = configured(
        tmp_path,
        "MOR_WINLINK_CALL=W1AW\nMOR_WINLINK_ADMIN_PASSWORD=a-long-admin-password\nMOR_WINLINK_CMS_PASSWORD=Gw.Pass+1\nMOR_PACKET_PTT=cm108\n",
        "MOR_WINLINK_CALL=W1AW\nMOR_PACKET_PTT=cm108\n",
    )
    modem_env, gateway_env = services["direwolf"]["environment"], services["winlink"]["environment"]
    assert not [name for name in modem_env if "PASSWORD" in name]
    assert "a-long-admin-password" not in json.dumps(services["direwolf"]) and "Gw.Pass+1" not in json.dumps(services["direwolf"])
    assert modem_env["MOR_WINLINK_CALL"] == "W1AW" and modem_env["MOR_PACKET_PTT"] == "cm108"
    assert gateway_env["MOR_WINLINK_ADMIN_PASSWORD"] == "a-long-admin-password" and gateway_env["MOR_WINLINK_CMS_PASSWORD"] == "Gw.Pass+1"


# -- direwolf.env from an existing winlink.env ------------------------------------------------------------------------------


@pytest.fixture
def folder(tmp_path):
    copy = tmp_path / "docker"
    copy.mkdir()
    shutil.copy(DOCKER / "split-winlink-env.sh", copy / "split-winlink-env.sh")
    return copy


def split(folder):
    return subprocess.run([SH, str(folder / "split-winlink-env.sh")], capture_output=True, text=True, timeout=20)


OLD = """# my settings
MOR_WINLINK_CALL=W1AW
MOR_WINLINK_SSID=10
MOR_WINLINK_LOCATOR=FN31pr
MOR_WINLINK_ADMIN_PASSWORD=a-long-admin-password
MOR_WINLINK_CMS_PASSWORD=Gw.Pass+1
MOR_WINLINK_KISS_HOST=192.168.1.50
MOR_PACKET_PTT=cm108
MOR_PACKET_PTT_GPIO=4
"""


def test_an_existing_winlink_env_gives_the_modem_its_call_and_ptt_and_not_one_password(folder):
    (folder / "winlink.env").write_text(OLD)
    result = split(folder)
    assert result.returncode == 0, result.stderr
    made = (folder / "direwolf.env").read_text()
    lines = [line for line in made.splitlines() if line and not line.startswith("#")]
    assert lines == ["MOR_WINLINK_CALL=W1AW", "MOR_WINLINK_SSID=10", "MOR_PACKET_PTT=cm108", "MOR_PACKET_PTT_GPIO=4"]
    for secret in ("a-long-admin-password", "Gw.Pass+1", "PASSWORD", "LOCATOR", "KISS_HOST"):
        assert secret not in made
    assert stat.S_IMODE((folder / "direwolf.env").stat().st_mode) == 0o600
    assert "direwolf.env" in result.stdout and "a-long-admin-password" not in result.stdout + result.stderr


def test_the_modem_renders_the_same_configuration_from_the_split_file_as_from_the_old_one(folder, tmp_path):
    (folder / "winlink.env").write_text(OLD)
    split(folder)
    env = dict(line.split("=", 1) for line in (folder / "direwolf.env").read_text().splitlines() if line and not line.startswith("#"))
    from_split, config = modem(tmp_path, **env, MOR_PACKET_PTT_DEVICE="/dev/zero")
    both = dict(line.split("=", 1) for line in OLD.splitlines() if "=" in line)
    from_old, config_old = modem(tmp_path, **both, MOR_PACKET_PTT_DEVICE="/dev/zero")
    assert from_split.returncode == 0 == from_old.returncode and config == config_old and "PTT CM108 4 /dev/zero" in config


def test_a_direwolf_env_the_owner_has_is_never_overwritten(folder):
    (folder / "winlink.env").write_text(OLD)
    (folder / "direwolf.env").write_text("MOR_WINLINK_CALL=K1ABC\n")
    result = split(folder)
    assert result.returncode == 0 and (folder / "direwolf.env").read_text() == "MOR_WINLINK_CALL=K1ABC\n" and result.stdout == ""


def test_without_a_winlink_env_it_does_nothing(folder):
    result = split(folder)
    assert result.returncode == 0 and result.stdout == "" and not (folder / "direwolf.env").exists()


def test_running_it_twice_changes_nothing(folder):
    (folder / "winlink.env").write_text(OLD)
    split(folder)
    first = (folder / "direwolf.env").read_text()
    again = split(folder)
    assert (folder / "direwolf.env").read_text() == first and again.stdout == ""


def test_the_modem_without_its_settings_says_where_to_get_them(tmp_path):
    result, cfg = run("direwolf-entrypoint.sh", tmp_path, {"MOR_PACKET_CARD": "2"})
    assert result.returncode == 2 and cfg is None
    assert "direwolf.env" in result.stderr and "split-winlink-env.sh" in result.stderr


def test_setup_and_update_both_make_the_modems_file_and_the_script_is_valid_shell():
    assert "split-winlink-env.sh" in (DOCKER / "update.sh").read_text() and "split-winlink-env.sh" in (DOCKER / "setup.sh").read_text()
    assert subprocess.run([SH, "-n", str(DOCKER / "split-winlink-env.sh")]).returncode == 0
    assert os.access(DOCKER / "split-winlink-env.sh", os.X_OK)


# -- /dev/null is not a PTT device ------------------------------------------------------------------------------------------


NULL = "/dev/null"


def ptt(tmp_path, device, **env):
    return modem(tmp_path, MOR_PACKET_PTT="cm108", MOR_PACKET_PTT_DEVICE=str(device), **env)


def test_cm108_ptt_with_nothing_mapped_is_refused_not_started_without_a_way_to_key(tmp_path):
    """compose maps /dev/null at /dev/packet-ptt when MOR_PACKET_PTT_HIDRAW is unset, and /dev/null is a character device."""
    link = tmp_path / "packet-ptt"
    link.symlink_to(NULL)
    result, cfg = ptt(tmp_path, link)
    assert result.returncode == 2 and cfg is None
    assert "no PTT device" in result.stderr and "/dev/null" in result.stderr and "MOR_PACKET_PTT_HIDRAW" in result.stderr


def test_the_same_numbers_as_dev_null_are_refused_even_when_it_is_a_different_file(tmp_path):
    """Inside the container the mapped node is a new node with /dev/null's device numbers, which `-ef` cannot see."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "stat").write_text("#!/bin/sh\necho 1:3\n")  # every file looks like major 1, minor 3
    (bin_dir / "stat").chmod(0o755)
    result = subprocess.run(
        [SH, str(DOCKER / "direwolf-entrypoint.sh")], capture_output=True, text=True, timeout=20,
        env={"PATH": f"{bin_dir}:/usr/bin:/bin", "MOR_WINLINK_CALL": "W1AW", "MOR_PACKET_CARD": "2", "MOR_PACKET_PTT": "cm108",
             "MOR_PACKET_PTT_DEVICE": "/dev/zero", "MOR_DIREWOLF_CONFIG": str(tmp_path / "d.conf"), "MOR_DIREWOLF_RENDER_ONLY": "1"},
    )
    assert result.returncode == 2 and "no PTT device" in result.stderr and not (tmp_path / "d.conf").exists()


def test_a_device_that_is_missing_or_not_a_device_is_still_refused(tmp_path):
    plain = tmp_path / "plain"
    plain.write_text("")
    for device in (tmp_path / "missing", plain):
        result, cfg = ptt(tmp_path, device)
        assert result.returncode == 2 and "no PTT device" in result.stderr and cfg is None, device


def test_a_real_device_is_used_as_the_ptt(tmp_path):
    result, cfg = ptt(tmp_path, "/dev/zero")  # a character device that is not /dev/null
    assert result.returncode == 0, result.stderr
    assert "PTT CM108 3 /dev/zero" in cfg


def test_no_ptt_is_not_affected_by_the_null_device(tmp_path):
    link = tmp_path / "packet-ptt"
    link.symlink_to(NULL)
    result, cfg = modem(tmp_path, MOR_PACKET_PTT="none", MOR_PACKET_PTT_DEVICE=str(link))
    assert result.returncode == 0 and "PTT" not in cfg


def test_the_health_check_is_unhealthy_when_cm108_is_asked_for_and_the_device_is_null(tmp_path):
    link = tmp_path / "packet-ptt"
    link.symlink_to(NULL)
    result = subprocess.run(
        [SH, str(DOCKER / "direwolf-entrypoint.sh"), "--check"], capture_output=True, text=True, timeout=20,
        env={"PATH": "/usr/bin:/bin", "MOR_PACKET_PTT": "cm108", "MOR_PACKET_PTT_DEVICE": str(link)},
    )
    assert result.returncode == 1


def test_the_message_is_for_the_log_so_it_says_how_to_fix_it(tmp_path):
    result, _ = ptt(tmp_path, tmp_path / "missing")
    assert "./setup.sh --packet-radio" in result.stderr


# -- the docs say what the log holds ----------------------------------------------------------------------------------------


def test_the_docs_say_that_frame_text_reaches_dockers_log_on_the_card_and_how_to_avoid_it():
    text = " ".join((DOCS / "winlink.md").read_text().split())
    assert "nothing wears the SD card" not in text and "there is no message store to back up or to wear the SD card" not in text
    assert "Docker's log" in text and "30 MB" in text and "the text of the frames" in text
    assert "driver: none" in text and "docker compose logs direwolf" in text
    assert "never kept" not in (DOCKER / "direwolf-entrypoint.sh").read_text()
