"""The core is given the host's kernel log read-only, and a host that has none (or an owner who does not want it) still starts."""
import re
from pathlib import Path

import yaml

DOCKER = Path(__file__).resolve().parents[2] / "docker"
SERVICES = yaml.safe_load((DOCKER / "compose.yaml").read_text())["services"]
KMSG = [d for d in SERVICES["core"]["devices"] if "/dev/kmsg" in str(d)]


def test_the_core_gets_exactly_one_kernel_log_device_and_nothing_else_does():
    assert len(KMSG) == 1
    others = [name for name, service in SERVICES.items() if name != "core" and "/dev/kmsg" in str(service.get("devices", []))]
    assert others == []


def test_it_is_mapped_read_only():
    assert KMSG[0].endswith(":/dev/kmsg:r")  # a bare `/dev/kmsg:/dev/kmsg` would be read-write-mknod


def test_it_appears_in_the_container_as_dev_kmsg_whatever_the_host_gives():
    host, container, mode = KMSG[0].rsplit(":", 2)
    assert container == "/dev/kmsg" and mode == "r" and host.startswith("${MOR_KMSG_DEVICE")


def test_the_default_is_the_null_device_so_a_host_without_dev_kmsg_still_starts():
    match = re.fullmatch(r"\$\{MOR_KMSG_DEVICE:-(.+?)\}:/dev/kmsg:r", KMSG[0])
    assert match and match.group(1) == "/dev/null"  # `:-`, so an empty value in .env falls back too


def test_the_core_reads_the_same_path_the_container_gets():
    from core.host_facts import KMSG_PATH

    assert str(KMSG_PATH) == "/dev/kmsg"


def test_the_core_still_runs_with_no_capabilities_and_as_an_ordinary_user():
    core = SERVICES["core"]
    assert core["cap_drop"] == ["ALL"] and core["user"] == "999:999" and not core.get("privileged") and core.get("read_only") is True


def test_the_example_env_file_tells_the_owner_about_it_and_how_to_turn_it_off():
    example = (DOCKER / ".env.example").read_text()
    assert "MOR_KMSG_DEVICE=/dev/kmsg" in example and "/dev/null" in example


def test_setup_sets_the_variable_compose_reads():
    setup = (DOCKER / "setup.sh").read_text()
    assert 'set_value .env MOR_KMSG_DEVICE' in setup
