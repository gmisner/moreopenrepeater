"""The compose file keeps the rule the architecture sets for logging (#221, #229): the radio never depends on it."""
from pathlib import Path

import yaml

DOCKER = Path(__file__).resolve().parents[2] / "docker"
COMPOSE = yaml.safe_load((DOCKER / "compose.yaml").read_text())
SERVICES = COMPOSE["services"]


def depends_on(service: dict) -> set[str]:
    declared = service.get("depends_on") or []
    return set(declared if isinstance(declared, list) else declared.keys())


def test_nothing_depends_on_the_collector():
    """If the core waited for logd, or the API did, a collector that failed to start would stop the radio or
    its dashboard. The senders drop and count instead."""
    assert [name for name, service in SERVICES.items() if "logd" in depends_on(service)] == []


def test_the_collector_has_no_network_and_nothing_it_does_not_need():
    logd = SERVICES["logd"]
    assert logd["network_mode"] == "none"  # it listens on Unix sockets only
    assert logd["read_only"] is True and logd["cap_drop"] == ["ALL"] and "no-new-privileges:true" in logd["security_opt"]
    assert "devices" not in logd and "ports" not in logd and "privileged" not in logd
    assert set(logd["volumes"]) == {"logd-data:/var/lib/logd", "logd-run:/run/logd"}


def test_the_radio_s_processes_come_before_it_for_cpu():
    assert SERVICES["logd"]["cpu_shares"] <= 256  # the default is 1024


def test_the_core_and_the_api_send_to_the_socket_in_the_volume_they_share_with_it():
    for name in ("core", "api"):
        service = SERVICES[name]
        assert service["environment"]["MOREOPENREPEATER_LOGD_SOCKET"] == "/run/logd/events.sock"
        assert "logd-run:/run/logd" in service["volumes"]
    assert "logd-run" in COMPOSE["volumes"] and "logd-data" in COMPOSE["volumes"]


def test_the_socket_the_senders_use_is_the_one_the_collector_listens_on():
    image = (DOCKER / "Dockerfile.logd").read_text()
    assert "MOREOPENREPEATER_LOGD_SOCKET=/run/logd/events.sock" in image
    assert "MOREOPENREPEATER_LOGD_QUERY_SOCKET=/run/logd/query.sock" in image


def test_every_image_creates_the_socket_directory_owned_by_the_user_so_the_volume_starts_that_way():
    for name in ("Dockerfile.core", "Dockerfile.api", "Dockerfile.logd"):
        text = (DOCKER / name).read_text()
        assert "/run/logd" in text and "chown 999:999" in text, name


def test_json_logging_is_on_for_all_three():
    assert [n for n in ("core", "api", "logd") if SERVICES[n]["environment"].get("MOREOPENREPEATER_LOG_FORMAT") != "json"] == []


def test_the_collector_image_installs_only_what_it_imports():
    lines = (DOCKER / "logd-requirements.txt").read_text().splitlines()
    requirements = [line.strip() for line in lines if line.strip() and not line.startswith("#")]
    assert requirements == ["pydantic>=2"]  # no numpy, audio or web framework
