"""Prebuilt images (issue #367): docker/images.sh, update.sh --registry, --no-registry and --rollback, and setup.sh --registry.

By default nothing changes: images are built here and tagged :local. With MOR_REGISTRY in .env the published services are
pulled, at the tag of the checkout's commit, and only the services the project does not publish are built. A failed pull must
leave the running containers, and the tag that matches them, alone."""
import re
from pathlib import Path

import yaml

DOCKER = Path(__file__).resolve().parents[2] / "docker"
COMPOSE = yaml.safe_load((DOCKER / "compose.yaml").read_text())
WORKFLOW = yaml.safe_load((DOCKER.parent / ".github/workflows/images.yml").read_text())
PUBLISHED = WORKFLOW["jobs"]["build"]["strategy"]["matrix"]["image"]
REGISTRY = "ghcr.io/gmisner/"


def tag_of(commit):
    return f"sha-{commit[:12]}"


def prepare_setup(checkout):
    (checkout.root / "ptt").write_text("")
    (checkout.docker / ".env").write_text(f"MOR_PTT_HIDRAW={checkout.root / 'ptt'}\nMOR_DATA_DIR={checkout.root / 'data'}\n")


# --- compose and the shared script ---------------------------------------------------------------------------------------


def test_published_services_are_named_from_the_registry_and_tag_and_the_rest_are_always_local():
    for name, service in COMPOSE["services"].items():
        if name in PUBLISHED:
            assert service["image"] == f"${{MOR_REGISTRY:-}}moreopenrepeater-{name}:${{MOR_TAG:-local}}", name
        else:
            assert service["image"] == f"moreopenrepeater-{name}:local", name


def test_with_nothing_set_the_names_are_the_ones_a_local_build_has_always_had():
    for name in PUBLISHED:
        resolved = COMPOSE["services"][name]["image"].replace("${MOR_REGISTRY:-}", "").replace("${MOR_TAG:-local}", "local")
        assert resolved == f"moreopenrepeater-{name}:local"


def test_the_script_and_the_workflow_agree_on_what_is_published():
    listed = re.search(r'^PUBLISHED_SERVICES="([^"]*)"', (DOCKER / "images.sh").read_text(), re.M).group(1).split()
    assert sorted(listed) == sorted(PUBLISHED)


def test_the_channel_is_given_to_the_api_when_it_starts_not_baked_into_the_image():
    assert COMPOSE["services"]["api"]["environment"]["MOREOPENREPEATER_BRANCH"] == "${MOR_BRANCH:-}"


# --- turning it on and off -------------------------------------------------------------------------------------------------


def test_turning_it_on_records_the_registry_and_this_commits_tag_and_starts_nothing(checkout):
    result = checkout.run("update.sh", "--registry")
    assert result.returncode == 0, result.stderr
    assert checkout.env_value("MOR_REGISTRY") == REGISTRY
    assert checkout.env_value("MOR_TAG") == tag_of(checkout.head())
    assert checkout.env_value("MOR_PREVIOUS_TAG") is None
    assert checkout.compose_calls() == []


def test_the_registry_owner_is_lowercase_and_comes_from_the_github_remote(checkout):
    checkout.git("config", "remote.origin.url", "https://github.com/SomeOne/a-fork.git")
    assert checkout.run("update.sh", "--registry").returncode == 0
    assert checkout.env_value("MOR_REGISTRY") == "ghcr.io/someone/"


def test_a_remote_that_is_not_github_is_refused_with_nothing_written(checkout):
    checkout.git("config", "remote.origin.url", "/srv/git/moreopenrepeater.git")
    result = checkout.run("update.sh", "--registry")
    assert result.returncode == 2
    assert "not a GitHub repository" in result.stderr
    assert checkout.env_value("MOR_REGISTRY") is None


def test_turning_it_off_removes_the_lines_and_the_next_update_builds_here_again(checkout):
    checkout.run("update.sh", "--registry")
    assert checkout.run("update.sh", "--no-registry").returncode == 0
    for name in ("MOR_REGISTRY", "MOR_TAG", "MOR_PREVIOUS_TAG"):
        assert checkout.env_value(name) is None
    checkout.run("update.sh")
    assert checkout.builds() == ["docker compose up -d --build"]


def test_setup_with_the_flag_uses_the_images_and_never_builds_the_published_ones(checkout):
    prepare_setup(checkout)
    result = checkout.run("setup.sh", "--callsign", "W1AW", "--registry")
    assert result.returncode == 0, result.stderr
    assert checkout.env_value("MOR_REGISTRY") == REGISTRY
    assert checkout.env_value("MOR_TAG") == tag_of(checkout.head())
    calls = checkout.compose_calls()
    assert "docker compose pull core api logd piper" in calls
    assert checkout.builds() == []
    assert "docker compose up -d" in calls


def test_setup_without_the_flag_is_unchanged(checkout):
    prepare_setup(checkout)
    assert checkout.run("setup.sh", "--callsign", "W1AW").returncode == 0
    assert checkout.env_value("MOR_REGISTRY") is None
    assert checkout.builds() == ["docker compose up -d --build"]
    assert not [c for c in checkout.compose_calls() if " pull" in c]


def test_setup_says_so_and_stops_when_the_images_cannot_be_pulled(checkout):
    prepare_setup(checkout)
    (checkout.root / "pull-fails").write_text("")
    result = checkout.run("setup.sh", "--callsign", "W1AW", "--registry")
    assert result.returncode == 2
    assert "couldn't pull the prebuilt images" in result.stderr
    assert not [c for c in checkout.compose_calls() if c.endswith(" up -d")]


# --- updating --------------------------------------------------------------------------------------------------------------


def test_an_update_pulls_the_new_commits_images_and_builds_only_what_is_not_published(checkout):
    checkout.ps_services.write_text("core\napi\nallstar\n")
    checkout.run("update.sh", "--registry")
    before = tag_of(checkout.head())
    newest = checkout.push_from_elsewhere("A newer commit")
    result = checkout.run("update.sh")
    assert result.returncode == 0, result.stderr
    assert checkout.env_value("MOR_TAG") == tag_of(newest)
    assert checkout.env_value("MOR_PREVIOUS_TAG") == before
    calls = checkout.compose_calls()
    assert "docker compose --profile allstar pull core api logd piper" in calls
    assert "docker compose --profile allstar build allstar" in calls
    assert calls[-1] == "docker compose --profile allstar up -d"
    assert checkout.builds() == []  # never `up --build`: that would build the pulled services here too
    assert checkout.index("git pull --ff-only") < checkout.index("docker compose --profile allstar pull")


def test_updating_to_the_same_commit_keeps_the_previous_tag(checkout):
    checkout.run("update.sh", "--registry")
    first = checkout.push_from_elsewhere("One")
    checkout.run("update.sh")
    second = checkout.push_from_elsewhere("Two")
    checkout.run("update.sh")
    assert checkout.env_value("MOR_PREVIOUS_TAG") == tag_of(first)
    checkout.run("update.sh")  # nothing new
    assert checkout.env_value("MOR_TAG") == tag_of(second)
    assert checkout.env_value("MOR_PREVIOUS_TAG") == tag_of(first)


def test_a_failed_pull_puts_the_old_tag_back_and_starts_nothing(checkout):
    checkout.run("update.sh", "--registry")
    running = checkout.env_value("MOR_TAG")
    checkout.push_from_elsewhere("A commit with no images")
    (checkout.root / "pull-fails").write_text("")
    result = checkout.run("update.sh")
    assert result.returncode == 2
    assert "couldn't pull the prebuilt images for this commit" in result.stderr
    assert "--no-registry" in result.stderr
    assert checkout.env_value("MOR_TAG") == running  # still what the running containers are
    assert not [c for c in checkout.compose_calls() if c.endswith(" up -d")]


# --- rolling back ----------------------------------------------------------------------------------------------------------


def test_rollback_goes_back_to_the_previous_images_without_moving_the_checkout(checkout):
    checkout.run("update.sh", "--registry")
    old = tag_of(checkout.head())
    newest = checkout.push_from_elsewhere("A bad release")
    checkout.run("update.sh")
    head = checkout.head()
    result = checkout.run("update.sh", "--rollback")
    assert result.returncode == 0, result.stderr
    assert checkout.env_value("MOR_TAG") == old
    assert checkout.env_value("MOR_PREVIOUS_TAG") == tag_of(newest)
    assert checkout.head() == head
    assert checkout.compose_calls()[-1] == "docker compose up -d"


def test_rollback_with_no_earlier_images_says_so_and_changes_nothing(checkout):
    checkout.run("update.sh", "--registry")
    result = checkout.run("update.sh", "--rollback")
    assert result.returncode == 2
    assert "no earlier images are recorded" in result.stderr
    assert not [c for c in checkout.compose_calls() if c.endswith(" up -d")]


def test_rollback_on_an_install_that_builds_here_says_so(checkout):
    result = checkout.run("update.sh", "--rollback")
    assert result.returncode == 2
    assert "builds its images on this machine" in result.stderr


def test_a_rollback_that_cannot_pull_changes_nothing(checkout):
    checkout.run("update.sh", "--registry")
    checkout.push_from_elsewhere()
    checkout.run("update.sh")
    current, previous = checkout.env_value("MOR_TAG"), checkout.env_value("MOR_PREVIOUS_TAG")
    (checkout.root / "pull-fails").write_text("")
    result = checkout.run("update.sh", "--rollback")
    assert result.returncode == 2
    assert (checkout.env_value("MOR_TAG"), checkout.env_value("MOR_PREVIOUS_TAG")) == (current, previous)
