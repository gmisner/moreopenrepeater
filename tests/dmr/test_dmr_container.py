"""The DMR container stands on its own (#221, #229): nothing from the host, nothing published, nothing unpinned."""
import hashlib
import ipaddress
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
DOCKER = ROOT / "docker"
COMPOSE = yaml.safe_load((DOCKER / "compose.yaml").read_text())
SERVICE = COMPOSE["services"]["dmr"]
DOCKERFILE = (DOCKER / "Dockerfile.dmr").read_text()
CODE = "\n".join(line for line in DOCKERFILE.splitlines() if not line.lstrip().startswith("#"))


def default_of(value: str) -> str:
    match = re.fullmatch(r"\$\{[A-Z_]+:-([^}]*)\}", value)
    assert match, value
    return match.group(1)


def test_it_is_off_unless_asked_for_and_nothing_waits_for_it():
    assert SERVICE["profiles"] == ["dmr"]
    for name, other in COMPOSE["services"].items():
        if name != "dmr":
            assert "dmr" not in str(other.get("depends_on", "")), name


def test_it_has_nothing_from_the_host_and_publishes_nothing():
    assert "network_mode" not in SERVICE and "ports" not in SERVICE and "expose" not in SERVICE
    for key in ("devices", "privileged", "pid", "ipc", "cgroup", "device_cgroup_rules", "sysctls"):
        assert key not in SERVICE, key
    for volume in SERVICE["volumes"]:
        assert not volume.startswith(("/", ".", "~")), volume  # no host path
    assert set(SERVICE["volumes"]) == {"dmr-config:/etc/moreopenrepeater/dmr", "dmr-control:/dmr-control"}
    assert {"dmr-config", "dmr-control"} <= set(COMPOSE["volumes"])


def test_it_is_on_the_private_network_at_a_fixed_address_inside_the_subnet_and_distinct():
    assert list(SERVICE["networks"]) == ["services"]
    subnet = ipaddress.ip_network(default_of(COMPOSE["networks"]["services"]["ipam"]["config"][0]["subnet"]))
    addresses = {
        name: ipaddress.ip_address(default_of(COMPOSE["services"][name]["networks"]["services"]["ipv4_address"]))
        for name in ("core", "allstar", "dmr")
    }
    assert all(a in subnet for a in addresses.values()) and len(set(addresses.values())) == 3
    assert str(addresses["dmr"]) == "172.28.0.30"


def test_the_bridge_is_told_where_the_core_is_by_the_same_expression_the_core_is_given_its_address_by():
    core_ip = COMPOSE["services"]["core"]["networks"]["services"]["ipv4_address"]
    assert SERVICE["environment"]["MOR_CORE_ADDRESS"].split("#")[0].strip() == core_ip


def test_compose_does_not_switch_dmr_audio_on_for_everyone():
    """The core listens for the bridge's audio only when the owner sets the peer in core.env."""
    for name in ("core", "api"):
        environment = COMPOSE["services"][name]["environment"]
        assert not [key for key in environment if "DMR" in key], name


def test_it_is_locked_down_and_a_quarter_of_the_default_cpu():
    assert SERVICE["read_only"] is True and SERVICE["cap_drop"] == ["ALL"] and SERVICE["user"] == "999:999"
    assert "no-new-privileges:true" in SERVICE["security_opt"] and "seccomp=unconfined" not in SERVICE["security_opt"]
    assert SERVICE["restart"] == "unless-stopped" and SERVICE["init"] is True
    assert SERVICE["cpu_shares"] <= 256  # the audio engine's processes come first
    for tmpfs in SERVICE["tmpfs"]:
        assert "mode=01777" in tmpfs  # a tmpfs is root's by default and this runs as 999


def test_the_image_pins_everything_it_installs():
    assert re.search(r"^FROM debian:trixie-slim@sha256:[0-9a-f]{64}$", DOCKERFILE, re.M)
    keyring_sha = re.search(r"ARG DVSWITCH_KEYRING_SHA256=([0-9a-f]{64})", DOCKERFILE).group(1)
    assert hashlib.sha256((DOCKER / "dvswitch-keyring.gpg").read_bytes()).hexdigest() == keyring_sha  # the file in the repo is the pinned one
    assert "sha256sum -c" in CODE and "--with-colons" in CODE and 'grep -q "$DVSWITCH_SIGNER"' in CODE  # and the key is checked
    for name in ("ANALOG_BRIDGE", "MMDVM_BRIDGE", "MD380_EMU"):
        assert re.search(rf"ARG {name}_VERSION=\S+", DOCKERFILE), name
    assert '"analog-bridge=$ANALOG_BRIDGE_VERSION" "mmdvm-bridge=$MMDVM_BRIDGE_VERSION" "md380-emu=$MD380_EMU_VERSION"' in CODE
    assert "--no-install-recommends" in CODE


def test_the_repository_is_added_directly_over_a_signed_source_and_dvswitchs_script_is_never_run():
    assert "signed-by=/usr/share/keyrings/dvswitch-keyring.gpg" in CODE
    assert "dvswitch.org/DVSwitch_Repository bookworm hamradio" in CODE
    assert "curl" not in CODE and "wget" not in CODE and not re.search(r"\|\s*(ba)?sh\b", CODE)  # no remote script is fetched or piped to a shell


def test_the_install_time_stand_ins_do_not_stay_and_the_build_tools_go():
    assert "rm -f /usr/bin/systemctl /usr/sbin/policy-rc.d" in CODE and "apt-get purge -y --auto-remove gnupg" in CODE
    assert "rm -rf /var/lib/apt/lists/*" in CODE


def test_the_image_needs_no_kernel_modules_devices_or_sound():
    assert "dkms" not in CODE.lower() and "/dev/" not in CODE and "snd" not in CODE


def test_the_image_runs_as_the_shared_user_publishes_nothing_and_checks_its_health():
    assert re.search(r"^USER 999:999$", DOCKERFILE, re.M)
    assert not re.findall(r"^EXPOSE ", DOCKERFILE, re.M)  # outbound connections to the DMR networks only
    assert 'CMD ["python3", "/usr/local/bin/dmr-supervisor.py", "--check"]' in DOCKERFILE
    assert 'VOLUME ["/etc/moreopenrepeater/dmr", "/dmr-control"]' in DOCKERFILE
    assert "chown 999:999 /etc/moreopenrepeater/dmr /dmr-control /run/dmr" in CODE  # a new named volume starts owned by 999


def test_every_file_the_image_copies_is_in_the_build_context_and_exists():
    allowed = {line[1:] for line in (ROOT / ".dockerignore").read_text().splitlines() if line.startswith("!")}
    copied = []
    for line in CODE.splitlines():
        if line.startswith("COPY "):
            *sources, _destination = line.split()[1:]
            copied += sources
    assert copied
    for source in copied:
        assert source in allowed, f"{source} is not allowed by .dockerignore"
        assert (ROOT / source).exists(), source


def test_the_volume_paths_match_what_the_dashboard_and_the_supervisor_use():
    """The API's restart request and status live in the control volume; the supervisor reads and writes the same files."""
    supervisor = (DOCKER / "dmr-supervisor.py").read_text()
    assert 'CONTROL_DIR = Path("/dmr-control")' in supervisor and 'CONFIG_DIR = Path("/etc/moreopenrepeater/dmr")' in supervisor
    assert '"request"' in supervisor and '"status.json"' in supervisor


def test_the_core_env_example_says_how_to_connect_the_bridges_audio():
    text = (DOCKER / "core.env.example").read_text()
    assert "# MOREOPENREPEATER_DMR_USRP_LISTEN=0.0.0.0:34011" in text and "# MOREOPENREPEATER_DMR_USRP_PEER=172.28.0.30:34012" in text
