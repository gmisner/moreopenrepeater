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


def test_the_api_reads_the_history_from_the_collector_s_query_socket():
    assert SERVICES["api"]["environment"]["MOREOPENREPEATER_LOGD_QUERY_SOCKET"] == "/run/logd/query.sock"
    assert "MOREOPENREPEATER_LOGD_QUERY_SOCKET=/run/logd/query.sock" in (DOCKER / "Dockerfile.logd").read_text()


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


def test_every_file_a_dockerfile_copies_is_in_the_build_context():
    """.dockerignore is an allowlist ('*' then '!path'); a COPY of anything not allowed fails only at build time."""
    import re

    root = Path(__file__).parents[2]
    allowed = [line[1:].strip().rstrip("/") for line in (root / ".dockerignore").read_text().splitlines() if line.startswith("!")]
    for dockerfile in (root / "docker").glob("Dockerfile.*"):
        for line in dockerfile.read_text().splitlines():
            match = re.match(r"\s*COPY\s+(?:--\S+\s+)*(\S+)\s+\S+", line)
            if match and not match.group(1).startswith("--from"):
                source = match.group(1).rstrip("/")
                assert any(source == a or source.startswith(a + "/") for a in allowed), f"{dockerfile.name} copies {source}, which .dockerignore excludes"


# -- the override that keeps the history in memory ----------------------------------------------------------

PROTECT = yaml.safe_load((DOCKER / "compose.protect-sd.yaml").read_text())


def test_the_memory_override_replaces_the_data_volume_by_mounting_over_the_same_path():
    """Compose merges `volumes` by target, so this is what makes the override replace logd-data."""
    base_target = next(v.split(":")[1] for v in SERVICES["logd"]["volumes"] if v.startswith("logd-data:"))
    tmpfs = next(v for v in PROTECT["services"]["logd"]["volumes"] if isinstance(v, dict) and v["type"] == "tmpfs")
    assert tmpfs["target"] == base_target == "/var/lib/logd"
    # The collector runs as user 999 and a tmpfs is root's, so it must be open to it. Written 1777 in YAML it is
    # decimal and wrong (found by running it): the file has to say 01777, which is read as octal.
    assert tmpfs["tmpfs"]["mode"] == 0o1777


def test_the_memory_override_keeps_errors_on_a_volume_the_image_made_writable_for_the_collector():
    logd = PROTECT["services"]["logd"]
    folder = logd["environment"]["MOREOPENREPEATER_LOGD_CRITICAL_DIR"]
    mounted = [v for v in logd["volumes"] if isinstance(v, str)]
    assert f"logd-critical:{folder}" in mounted and "logd-critical" in PROTECT["volumes"]
    dockerfile = (DOCKER / "Dockerfile.logd").read_text()
    assert folder in dockerfile and f"chown 999:999 /var/lib/logd {folder}" in dockerfile  # a new volume copies its owner


def test_the_memory_override_keeps_the_history_inside_the_memory_it_gives():
    logd = PROTECT["services"]["logd"]
    tmpfs = next(v for v in logd["volumes"] if isinstance(v, dict) and v["type"] == "tmpfs")
    megabytes = int(str(tmpfs["tmpfs"]["size"]).rstrip("m"))
    assert int(logd["environment"]["MOREOPENREPEATER_LOGD_MAX_MB"]) < megabytes


def test_the_memory_override_changes_nothing_but_the_collector():
    assert set(PROTECT["services"]) == {"logd"}
