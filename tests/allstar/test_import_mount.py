"""The compose file's mount for an existing AllStar node's configuration (docs/allstar.md, Importing a node)."""
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
DOCKER = ROOT / "docker"
COMPOSE = yaml.safe_load((DOCKER / "compose.yaml").read_text())
API = COMPOSE["services"]["api"]


def import_mounts():
    return [v for v in API["volumes"] if v.endswith("/import/asterisk:ro") or "/import/asterisk" in v]


def test_the_api_mounts_it_read_only_and_only_the_api_does():
    assert import_mounts() == ["${MOR_ALLSTAR_IMPORT_DIR:-./empty}:/import/asterisk:ro"]
    for name, service in COMPOSE["services"].items():
        if name != "api":
            assert not [v for v in service.get("volumes", []) if "/import/" in str(v)], name


def test_the_default_is_the_empty_folder_in_the_repo_so_docker_never_creates_one_on_the_host():
    default = import_mounts()[0].split(":-", 1)[1].split("}", 1)[0]
    assert default == "./empty"
    empty = DOCKER / "empty"
    assert empty.is_dir() and [p.name for p in empty.iterdir()] == [".gitkeep"]  # empty but for the file that lets git keep it


def test_the_empty_folder_is_committed_and_not_ignored_or_left_out_of_a_clone():
    tracked = subprocess.run(["git", "ls-files", "docker/empty"], cwd=ROOT, capture_output=True, text=True).stdout.split()
    ignored = subprocess.run(["git", "check-ignore", "-q", "docker/empty/.gitkeep"], cwd=ROOT).returncode == 0
    assert not ignored
    if (ROOT / ".git").exists():
        assert tracked == ["docker/empty/.gitkeep"]


def test_the_api_is_told_where_it_is_and_where_that_is_on_the_host():
    env = API["environment"]
    assert env["MOREOPENREPEATER_ALLSTAR_IMPORT_DIR"] == "/import/asterisk"
    assert env["MOREOPENREPEATER_ALLSTAR_IMPORT_SOURCE"] == "${MOR_ALLSTAR_IMPORT_DIR:-}"  # empty unless setup.sh named a folder


def test_the_mount_is_the_only_new_thing_on_the_api_and_the_node_container_gets_nothing():
    node = COMPOSE["services"]["allstar"]
    assert set(node["volumes"]) == {"allstar-config:/etc/asterisk", "allstar-data:/var/lib/asterisk"}
    assert "MOR_ALLSTAR_IMPORT" not in yaml.safe_dump(node)
    assert API["read_only"] is True and API["cap_drop"] == ["ALL"]  # the API stays locked down


def test_the_example_env_documents_it_commented_out():
    text = (DOCKER / ".env.example").read_text()
    assert "# MOR_ALLSTAR_IMPORT_DIR=/etc/asterisk" in text and "\nMOR_ALLSTAR_IMPORT_DIR=" not in text
