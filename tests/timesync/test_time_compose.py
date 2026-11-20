"""The time container in compose and in its Dockerfile: off by default, one capability, nothing published, each side writes only its own file."""
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
DOCKER = ROOT / "docker"
COMPOSE = yaml.safe_load((DOCKER / "compose.yaml").read_text())
SERVICES = COMPOSE["services"]
CHRONY = SERVICES["chrony"]
DOCKERFILE = (DOCKER / "Dockerfile.chrony").read_text()
CODE = "\n".join(line for line in DOCKERFILE.splitlines() if not line.lstrip().startswith("#"))


def mounts(service):
    """{volume: mode} for the named volumes a service mounts: 'rw' unless it says ro."""
    found = {}
    for entry in SERVICES[service].get("volumes", []):
        source, _, rest = entry.partition(":")
        target, _, mode = rest.partition(":")
        found[source] = (target, mode or "rw")
    return found


# -- off unless asked for -------------------------------------------------------------------------------------------------------------


def test_it_is_off_unless_asked_for_so_an_existing_install_is_untouched():
    assert CHRONY["profiles"] == ["time"]
    for name, other in SERVICES.items():
        if name != "chrony":
            assert "chrony" not in str(other.get("depends_on", "")), name  # nothing waits for it
    for name in ("core", "api", "logd", "piper"):
        assert "profiles" not in SERVICES[name], name


def test_it_is_the_last_service_so_other_branches_additions_do_not_collide_with_it():
    assert list(SERVICES)[-1] == "chrony"
    assert list(COMPOSE["volumes"])[-2:] == ["time-config", "time-status"]


# -- the privilege ----------------------------------------------------------------------------------------------------------------------


def test_the_capability_list_is_exactly_drop_all_then_add_sys_time():
    assert CHRONY["cap_drop"] == ["ALL"]
    assert CHRONY["cap_add"] == ["SYS_TIME"]  # the one that lets it set the host's clock, and nothing else


def test_no_other_service_is_given_any_capability_and_nothing_here_is_privileged():
    for name, service in SERVICES.items():
        if name != "chrony":
            assert "cap_add" not in service, name
        assert "privileged" not in service and "pid" not in service and "ipc" not in service and "cgroup" not in service, name
    assert "devices" not in CHRONY and "device_cgroup_rules" not in CHRONY and "sysctls" not in CHRONY


def test_it_is_locked_down_the_way_the_other_optional_containers_are():
    assert CHRONY["read_only"] is True
    assert "no-new-privileges:true" in CHRONY["security_opt"]
    assert not any("seccomp=unconfined" in option for option in CHRONY["security_opt"])  # Docker's default filter stays on
    assert CHRONY["restart"] == "unless-stopped" and CHRONY["init"] is True
    assert CHRONY["user"] == "0:0"  # root with one capability: the compose comment says why it is not another user


def test_the_compose_comment_says_what_the_privilege_is_and_why_it_is_this_small():
    text = (DOCKER / "compose.yaml").read_text()
    block = text[text.index("  # The time container, off by default"):text.index("  chrony:\n")]
    for phrase in ("THE PRIVILEGE", "CAP_SYS_TIME", "HOST's clock", "-U", "CAP_SETUID", "CAP_SETGID", "CAP_KILL", "root with one capability", "seccomp"):
        assert phrase in block, phrase
    assert "—" not in block


# -- nothing reachable ---------------------------------------------------------------------------------------------------------------------


def test_nothing_is_published_and_it_has_no_network_service_or_address_of_its_own():
    assert "ports" not in CHRONY and "expose" not in CHRONY
    assert "network_mode" not in CHRONY  # not the host's network
    assert CHRONY["networks"] == ["services"]  # the private network, outbound only: no fixed address for anything to reach it at
    assert "extra_hosts" not in CHRONY


def test_it_has_no_secret_and_no_env_file():
    assert "env_file" not in CHRONY and "environment" not in CHRONY


# -- who can write what ----------------------------------------------------------------------------------------------------------------------


def test_the_setting_is_written_by_the_api_and_only_read_by_the_time_container():
    assert mounts("chrony")["time-config"] == ("/time-config", "ro")
    assert mounts("api")["time-config"] == ("/time-config", "rw")


def test_the_status_is_written_by_the_time_container_and_only_read_by_the_api():
    assert mounts("chrony")["time-status"] == ("/time-status", "rw")
    assert mounts("api")["time-status"] == ("/time-status", "ro")


def test_no_other_service_sees_either_volume_and_each_is_a_named_volume_not_a_host_path():
    for name in SERVICES:
        if name not in ("api", "chrony"):
            assert not {"time-config", "time-status"} & set(mounts(name)), name
    assert {"time-config", "time-status"} <= set(COMPOSE["volumes"])
    for entry in CHRONY["volumes"]:
        assert not entry.startswith(("/", ".", "~")) and "docker.sock" not in entry


def test_exactly_one_service_can_write_each_volume():
    for volume in ("time-config", "time-status"):
        writers = [name for name in SERVICES if mounts(name).get(volume, (None, "ro"))[1] == "rw"]
        assert len(writers) == 1, (volume, writers)
    assert [n for n in SERVICES if mounts(n).get("time-config", (0, "ro"))[1] == "rw"] == ["api"]
    assert [n for n in SERVICES if mounts(n).get("time-status", (0, "ro"))[1] == "rw"] == ["chrony"]


def test_the_api_is_told_where_the_two_folders_are_and_they_are_the_mounts():
    env = {key: str(value).split("#")[0].strip() for key, value in SERVICES["api"]["environment"].items()}
    assert env["MOREOPENREPEATER_TIME_CONFIG_DIR"] == mounts("api")["time-config"][0] == "/time-config"
    assert env["MOREOPENREPEATER_TIME_STATUS_DIR"] == mounts("api")["time-status"][0] == "/time-status"


def test_everything_chronyd_writes_is_in_a_tmpfs_so_the_sd_card_is_not_written():
    tmpfs = [entry.split(":")[0] for entry in CHRONY["tmpfs"]]
    assert "/run/chrony" in tmpfs
    mode = [entry for entry in CHRONY["tmpfs"] if entry.startswith("/run/chrony")][0]
    assert "mode=0750" in mode  # chronyd (as root) refuses a socket folder that others can use
    supervisor = (DOCKER / "chrony-supervisor.py").read_text()
    assert 'MOR_CHRONY_RUN", "/run/chrony"' in supervisor and 'MOR_TIME_STATUS", "/time-status"' in supervisor and 'MOR_TIME_CONFIG", "/time-config"' in supervisor


# -- health ------------------------------------------------------------------------------------------------------------------------------------


def test_the_health_check_is_the_supervisors_own_and_the_image_agrees():
    assert CHRONY["healthcheck"]["test"] == ["CMD", "python3", "/usr/local/bin/chrony-supervisor.py", "--check"]
    assert re.search(r'HEALTHCHECK [^\n]*\\\n\s+CMD \["python3", "/usr/local/bin/chrony-supervisor.py", "--check"\]', DOCKERFILE)


# -- the image ---------------------------------------------------------------------------------------------------------------------------------


def test_the_image_is_pinned_by_digest_like_the_others_and_uses_debians_own_chrony():
    assert re.search(r"^FROM debian:trixie-slim@sha256:[0-9a-f]{64}$", DOCKERFILE, re.M)
    others = {re.search(r"^FROM (debian:trixie-slim@sha256:[0-9a-f]{64})", (DOCKER / name).read_text(), re.M).group(1) for name in ("Dockerfile.dmr", "Dockerfile.winlink")}
    assert re.search(r"^FROM (debian:trixie-slim@sha256:[0-9a-f]{64})", DOCKERFILE, re.M).group(1) in others  # the same base the other Debian images are on
    assert "apt-get install -y --no-install-recommends chrony python3" in CODE
    assert "rm -rf /var/lib/apt/lists/*" in CODE and "rm -f /usr/sbin/policy-rc.d" in CODE  # the install-time stand-in does not stay


def test_the_image_runs_the_supervisor_as_root_on_purpose_and_checks_the_programs_are_there():
    assert not re.search(r"^USER\b", CODE, re.M)  # chronyd must start as root: see compose.yaml
    assert 'ENTRYPOINT ["python3", "/usr/local/bin/chrony-supervisor.py"]' in CODE
    assert "test -x /usr/sbin/chronyd && test -x /usr/bin/chronyc" in CODE  # a package that moved its binary fails the build, not the first start
    assert "COPY docker/chrony-supervisor.py /usr/local/bin/chrony-supervisor.py" in CODE


def test_the_build_context_includes_the_supervisor():
    ignore = (ROOT / ".dockerignore").read_text().splitlines()
    assert "!docker/chrony-supervisor.py" in ignore
    for source in re.findall(r"^COPY (?!--from)(\S+) ", CODE, re.M):
        assert f"!{source}" in ignore, f"Dockerfile.chrony copies {source}, which .dockerignore leaves out"


def test_the_folders_exist_in_the_image_with_the_owners_that_let_either_container_create_the_volumes_first():
    assert "mkdir -p /time-config /time-status /run/chrony && chown 999:999 /time-config" in CODE  # the API's uid writes the setting
    api = (DOCKER / "Dockerfile.api").read_text()
    assert "/time-config /time-status" in api and "999:999 /data" in api
    assert re.search(r"chown 999:999 [^\n]*/time-config(?! /time-status)", api) and "chown 999:999 /data" in api
    assert not re.search(r"chown [^\n]*/time-status", api)  # root's: only the time container writes it


def test_the_supervisor_is_executable_and_valid_python():
    path = DOCKER / "chrony-supervisor.py"
    assert path.stat().st_mode & 0o100
    compile(path.read_text(), str(path), "exec")
    assert path.read_text().startswith("#!/usr/bin/env python3")
