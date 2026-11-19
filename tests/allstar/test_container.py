"""The AllStar container stands on its own (#221, #229): nothing from the host, and nothing reachable that need not be.

The owner's rule: each service is its own container and does not rely on the host. These are the guards that keep it so."""
import re
from pathlib import Path

import yaml

DOCKER = Path(__file__).resolve().parents[2] / "docker"
COMPOSE = yaml.safe_load((DOCKER / "compose.yaml").read_text())
SERVICE = COMPOSE["services"]["allstar"]
DOCKERFILE = (DOCKER / "Dockerfile.allstar").read_text()


def test_it_is_off_unless_asked_for_so_an_existing_install_is_untouched():
    assert SERVICE["profiles"] == ["allstar"]
    for name, other in COMPOSE["services"].items():
        if name != "allstar":
            assert "profiles" not in other and "allstar" not in str(other.get("depends_on", ""))  # nothing waits for it


def test_it_has_nothing_from_the_host():
    assert "network_mode" not in SERVICE  # not the host's network, and not none: its own
    assert "devices" not in SERVICE and "privileged" not in SERVICE and "pid" not in SERVICE and "ipc" not in SERVICE
    assert "cgroup" not in SERVICE and "device_cgroup_rules" not in SERVICE and "sysctls" not in SERVICE
    for volume in SERVICE["volumes"]:
        assert not volume.startswith(("/", ".", "~")), volume  # no bind mount of a host path: named volumes only
    assert set(SERVICE["volumes"]) == {"allstar-config:/etc/asterisk", "allstar-data:/var/lib/asterisk"}
    assert {"allstar-config", "allstar-data"} <= set(COMPOSE["volumes"])


def test_only_iax_is_published_and_the_management_interface_never_is():
    assert SERVICE["ports"] == ["${MOR_ALLSTAR_IAX_PORT:-4569}:4569/udp"]  # how AllStarLink nodes reach each other
    assert not [p for p in SERVICE["ports"] if "5038" in p]
    assert "127.0.0.1" not in str(SERVICE["ports"])  # and not published on the host's loopback either


def test_it_lives_on_the_services_network_which_has_a_configurable_subnet():
    assert list(SERVICE["networks"]) == ["services"]
    subnet = COMPOSE["networks"]["services"]["ipam"]["config"][0]["subnet"]
    assert subnet == "${MOR_SERVICES_SUBNET:-172.28.0.0/24}"
    assert SERVICE["environment"]["MOR_AMI_PERMIT"] == subnet  # the management interface answers exactly that network


def test_it_is_locked_down():
    assert SERVICE["read_only"] is True and SERVICE["cap_drop"] == ["ALL"] and SERVICE["user"] == "999:999"
    assert "no-new-privileges:true" in SERVICE["security_opt"] and "seccomp=unconfined" not in SERVICE["security_opt"]
    assert SERVICE["restart"] == "unless-stopped" and SERVICE["init"] is True
    for tmpfs in SERVICE["tmpfs"]:  # a tmpfs is root's by default and the node runs as 999
        assert "mode=01777" in tmpfs or tmpfs.startswith("/tmp")


def test_the_secret_is_not_in_the_compose_file_and_there_is_no_default_one():
    text = (DOCKER / "compose.yaml").read_text()
    assert "make4An1ce" not in text and "MOR_AMI_SECRET" not in yaml.safe_dump(SERVICE.get("environment", {}))
    example = (DOCKER / "allstar.env.example").read_text()
    assert re.search(r"^# MOR_AMI_SECRET=\s*$", example, re.M)  # commented out and empty: the owner must choose
    assert not re.search(r"^MOR_AMI_SECRET=\S", example, re.M)


def test_the_image_pins_what_it_installs():
    assert re.search(r"^FROM debian:trixie-slim@sha256:[0-9a-f]{64}$", DOCKERFILE, re.M)  # the base, by digest
    assert re.search(r"ARG ASL_REPOS_SHA256=[0-9a-f]{64}", DOCKERFILE) and "sha256sum -c" in DOCKERFILE  # the repository's package, by hash
    assert re.search(r"ARG ASL_VERSION=2:22\.\d+\.\d+\+asl3-\d+\.\d+\.\d+-\d+\.deb13", DOCKERFILE)  # the node software, by version
    assert 'asl3-asterisk=$ASL_VERSION' in DOCKERFILE and "--no-install-recommends" in DOCKERFILE


def test_the_image_needs_no_kernel_modules_or_sound():
    code = "\n".join(line for line in DOCKERFILE.splitlines() if not line.lstrip().startswith("#"))
    assert "dahdi-dkms" not in code and "dkms" not in code.lower() and "snd-pcm-oss" not in code
    assert "/dev/snd" not in code


def test_the_install_time_systemd_stand_ins_do_not_stay_in_the_image():
    code = "\n".join(line for line in DOCKERFILE.splitlines() if not line.lstrip().startswith("#"))
    assert "rm -f /usr/bin/systemctl /usr/sbin/policy-rc.d" in code  # a fake systemctl left behind would hide real failures
    assert "apt-get purge -y --auto-remove curl" in code and "rm -rf /var/lib/apt/lists/*" in code


def test_the_image_runs_as_the_shared_user_exposes_only_iax_and_checks_its_health():
    assert re.search(r"^USER 999:999$", DOCKERFILE, re.M)
    assert re.findall(r"^EXPOSE (.+)$", DOCKERFILE, re.M) == ["4569/udp"]
    assert "HEALTHCHECK" in DOCKERFILE and "core waitfullybooted" in DOCKERFILE
    assert 'VOLUME ["/etc/asterisk", "/var/lib/asterisk"]' in DOCKERFILE
    assert "chown -R 999:999 /etc/asterisk /var/lib/asterisk" in DOCKERFILE  # a new named volume starts owned by 999


def test_the_entrypoint_is_copied_into_the_image_and_allowed_by_the_build_context():
    assert "COPY docker/allstar-entrypoint.sh" in DOCKERFILE
    allowed = {line[1:] for line in (DOCKER.parent / ".dockerignore").read_text().splitlines() if line.startswith("!")}
    assert "docker/allstar-entrypoint.sh" in allowed


def test_the_settings_files_that_hold_secrets_cannot_be_committed_by_accident():
    ignored = (DOCKER.parent / ".gitignore").read_text().splitlines()
    for name in ("/docker/allstar.env", "/docker/api.env", "/docker/core.env", "/docker/logd.env", "/docker/.env"):
        assert name in ignored, name
    assert not [line for line in ignored if line.endswith(".example")]  # the examples are the ones that are tracked
