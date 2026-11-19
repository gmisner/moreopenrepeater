"""The core and the API on the private services network (#221, #229): nothing of the host's network is used.

The owner's rule is that no service relies on the host. The core used the host's network only so a host Asterisk could reach it
on the loopback; with the node in its own container both live on one private network, and the old layout is an override."""
import ipaddress
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

DOCKER = Path(__file__).resolve().parents[2] / "docker"
COMPOSE = yaml.safe_load((DOCKER / "compose.yaml").read_text())
SERVICES = COMPOSE["services"]
SUBNET_DEFAULT = "172.28.0.0/24"


def default_of(value: str) -> str:
    """`${NAME:-default}` -> default"""
    match = re.fullmatch(r"\$\{[A-Z_]+:-([^}]*)\}", value)
    assert match, value
    return match.group(1)


def test_the_core_and_the_api_are_on_the_private_network_not_the_hosts():
    for name in ("core", "api"):
        service = SERVICES[name]
        assert "network_mode" not in service, name  # not the host's network
        assert "services" in service["networks"], name
    assert SERVICES["logd"]["network_mode"] == "none"  # still no network at all


def test_the_core_is_not_published_at_all():
    assert "ports" not in SERVICES["core"]
    assert "expose" not in SERVICES["core"]


def test_the_dashboard_is_published_on_the_hosts_loopback_unless_the_owner_chooses_otherwise():
    """With no accounts set up the dashboard is open and can key the transmitter, so it is not offered to the network
    until the owner asks (MOR_API_HOST), exactly as it was with the host network."""
    assert SERVICES["api"]["ports"] == ["${MOR_API_HOST:-127.0.0.1}:${MOR_API_PORT:-8000}:8000"]
    assert default_of("${MOR_API_HOST:-127.0.0.1}") == "127.0.0.1"
    environment = SERVICES["api"]["environment"]
    assert environment["MOREOPENREPEATER_HOST"] == "0.0.0.0" and environment["MOREOPENREPEATER_PORT"] == "8000"  # inside the container


def test_the_apis_own_health_check_looks_where_it_listens():
    dockerfile = (DOCKER / "Dockerfile.api").read_text()
    assert "('127.0.0.1', 8000)" in dockerfile  # inside the container, whose own loopback the 0.0.0.0 listener answers


def test_the_fixed_addresses_are_inside_the_default_subnet_and_distinct():
    subnet = ipaddress.ip_network(default_of(COMPOSE["networks"]["services"]["ipam"]["config"][0]["subnet"]))
    assert str(subnet) == SUBNET_DEFAULT
    core = ipaddress.ip_address(default_of(SERVICES["core"]["networks"]["services"]["ipv4_address"]))
    node = ipaddress.ip_address(default_of(SERVICES["allstar"]["networks"]["services"]["ipv4_address"]))
    assert core in subnet and node in subnet and core != node
    assert subnet.network_address not in (core, node) and subnet.broadcast_address not in (core, node)
    assert ipaddress.ip_address("172.28.0.1") not in (core, node)  # the gateway


def test_the_core_and_the_api_agree_on_where_the_radios_audio_goes():
    """The API writes the node's configuration and the core listens: the same addresses in both, from one definition."""
    core, api = SERVICES["core"]["environment"], SERVICES["api"]["environment"]
    names = [
        "MOREOPENREPEATER_USRP_LISTEN", "MOREOPENREPEATER_USRP_ADDRESS",
        "MOREOPENREPEATER_AUDIOSOCKET_LISTEN", "MOREOPENREPEATER_AUDIOSOCKET_ADDRESS",
    ]
    for name in names:
        assert core[name] == api[name], name
    assert core["MOREOPENREPEATER_USRP_LISTEN"] == "0.0.0.0:34001"  # on the container's own network; the node's address is the only one heard
    core_ip = SERVICES["core"]["networks"]["services"]["ipv4_address"]
    assert core["MOREOPENREPEATER_USRP_ADDRESS"] == core_ip  # what Asterisk is told: the core's own address, one expression
    assert core["MOREOPENREPEATER_AUDIOSOCKET_ADDRESS"] == core_ip + ":9092"


def test_compose_switches_nothing_on_that_the_owner_has_not_asked_for():
    """AllStar audio and the node's AMI are on only when the owner sets them (core.env, api.env), so a bench with no
    node is unchanged by moving to the private network."""
    for name in ("core", "api"):
        environment = SERVICES[name]["environment"]
        assert not [key for key in environment if key.startswith("MOREOPENREPEATER_AMI_") or key == "MOREOPENREPEATER_USRP_NODE"]


@pytest.mark.parametrize("name", ["core.env.example", "api.env.example"])
def test_the_env_examples_point_at_the_node_by_name_and_set_no_secret(name):
    text = (DOCKER / name).read_text()
    assert "MOREOPENREPEATER_AMI_HOST=allstar" in text
    assert not re.search(r"^MOREOPENREPEATER_AMI_SECRET=\S", text, re.M)  # commented out and empty
    assert "# MOREOPENREPEATER_AMI_SECRET=" in text


def test_the_dashboards_port_is_not_left_in_api_env_where_it_would_be_the_wrong_one_inside_the_container():
    assert not re.search(r"^# MOREOPENREPEATER_PORT=", (DOCKER / "api.env.example").read_text(), re.M)


# -- the way back ------------------------------------------------------------------------------------------------

docker = shutil.which("docker")


def merged(*files):
    command = ["docker", "compose"]
    for name in files:
        command += ["-f", name]
    result = subprocess.run(command + ["config", "--format", "json"], cwd=DOCKER, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)["services"]


@pytest.mark.skipif(docker is None, reason="needs docker compose to merge the files")
def test_the_host_network_override_restores_the_old_layout_and_changes_nothing_else():
    old = merged("compose.yaml", "compose.host-network.yaml")
    new = merged("compose.yaml")
    for name in ("core", "api"):
        assert old[name]["network_mode"] == "host" and "networks" not in old[name], name
        assert old[name]["environment"]["MOREOPENREPEATER_USRP_LISTEN"] == "127.0.0.1:34001"
        assert old[name]["environment"]["MOREOPENREPEATER_AUDIOSOCKET_ADDRESS"] == "127.0.0.1:9092"
        assert "network_mode" not in new[name] and "services" in new[name]["networks"]
    assert "ports" not in old["api"]  # a host-network service publishes nothing
    assert old["api"]["environment"]["MOREOPENREPEATER_HOST"] == "127.0.0.1"  # loopback by default, as before
    assert old["api"]["environment"]["MOREOPENREPEATER_PORT"] == "8000"
    assert old["logd"] == new["logd"]  # the collector is the same either way
    for key in ("devices", "volumes", "user", "read_only", "cap_drop", "security_opt"):
        assert old["core"][key] == new["core"][key], key  # the hardening and the devices do not depend on the network


@pytest.mark.skipif(docker is None, reason="needs docker compose to merge the files")
def test_the_default_layout_merges_to_a_published_loopback_dashboard_and_fixed_addresses():
    services = merged("compose.yaml")
    api_port = services["api"]["ports"][0]
    assert (api_port["host_ip"], api_port["published"], api_port["target"]) == ("127.0.0.1", "8000", 8000)
    assert services["core"]["networks"]["services"]["ipv4_address"] == "172.28.0.10"
