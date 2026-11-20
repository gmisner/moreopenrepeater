"""docker/update.sh: the container install's update command.

The dashboard can't update the containers (it holds no Docker access), so this is what the Updates page tells you to
run. It must only ever move the checkout forward, say plainly when it can't, change nothing when it refuses, and write
the commit into .env BEFORE it builds, because the API image is built with that commit as a build argument."""
import re
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

DOCKER = Path(__file__).resolve().parents[2] / "docker"
SHA = re.compile(r"^[0-9a-f]{40}$")


def test_it_moves_forward_records_the_new_commit_and_only_then_builds(checkout):
    newest = checkout.push_from_elsewhere("A newer commit")
    result = checkout.run("update.sh")
    assert result.returncode == 0, result.stderr
    assert checkout.head() == newest
    assert checkout.env_value("MOR_COMMIT") == newest
    # The build saw the new commit already in .env: that is what compose hands the image as MOR_COMMIT.
    seen = [line for line in checkout.log() if line.startswith("env-at-build:")]
    assert seen == [f"env-at-build: MOR_COMMIT={newest} MOR_COMMIT_DATE={checkout.env_value('MOR_COMMIT_DATE')} MOR_BRANCH=main MOR_REPO=gmisner/moreopenrepeater "]
    assert checkout.index("git pull --ff-only") < checkout.index("docker compose up -d --build") < checkout.index("env-at-build:")


def test_it_pulls_with_ff_only_and_nothing_else(checkout):
    checkout.push_from_elsewhere()
    checkout.run("update.sh")
    pulls = [line for line in checkout.log() if line.startswith("git pull")]
    assert pulls == ["git pull --ff-only"]


def test_it_records_the_commit_date_the_branch_and_the_github_repo(checkout):
    checkout.run("update.sh")
    assert SHA.match(checkout.env_value("MOR_COMMIT"))
    assert checkout.env_value("MOR_COMMIT") == checkout.head()
    assert re.match(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(Z|[+-]\d\d:\d\d)$", checkout.env_value("MOR_COMMIT_DATE"))
    assert checkout.env_value("MOR_BRANCH") == "main"
    assert checkout.env_value("MOR_REPO") == "gmisner/moreopenrepeater"


def test_a_remote_url_with_a_token_in_it_is_never_written_down(checkout):
    checkout.git("config", "remote.origin.url", "https://someone:ghp_secrettoken@github.com/gmisner/moreopenrepeater.git")
    assert checkout.run("update.sh", "--record-only").returncode == 0
    text = (checkout.docker / ".env").read_text()
    assert "ghp_secrettoken" not in text and "someone" not in text
    assert checkout.env_value("MOR_REPO") is None  # unknown, so the page can't check GitHub, rather than a URL with a password


@pytest.mark.parametrize(
    "url,repo",
    [
        ("https://github.com/gmisner/moreopenrepeater", "gmisner/moreopenrepeater"),
        ("git@github.com:gmisner/moreopenrepeater.git", "gmisner/moreopenrepeater"),
        ("ssh://git@github.com/someone/a-fork.git", "someone/a-fork"),
        ("https://gitlab.com/gmisner/moreopenrepeater.git", None),
        ("/srv/git/moreopenrepeater.git", None),
    ],
)
def test_the_repo_is_recorded_for_github_remotes_only(checkout, url, repo):
    checkout.git("config", "remote.origin.url", url)
    assert checkout.run("update.sh", "--record-only").returncode == 0
    assert checkout.env_value("MOR_REPO") == repo


def test_the_channel_is_the_checkouts_branch(checkout):
    checkout.git("checkout", "-q", "-b", "beta")
    checkout.git("push", "-q", "-u", "origin", "beta")
    assert checkout.run("update.sh").returncode == 0
    assert checkout.env_value("MOR_BRANCH") == "beta"


def test_a_detached_checkout_has_no_branch_to_record_and_is_not_updated(checkout):
    checkout.git("checkout", "-q", "--detach")
    recorded = checkout.run("update.sh", "--record-only")
    assert recorded.returncode == 0
    assert checkout.env_value("MOR_BRANCH") is None
    assert checkout.env_value("MOR_COMMIT") == checkout.head()
    result = checkout.run("update.sh")
    assert result.returncode == 2
    assert "not on a branch that follows origin" in result.stderr
    assert checkout.builds() == []


def test_a_dirty_checkout_is_refused_with_nothing_changed(checkout):
    newest = checkout.push_from_elsewhere()
    before = checkout.head()
    tracked = checkout.docker / "setup.sh"
    tracked.write_text(tracked.read_text() + "\n# my own edit\n")
    result = checkout.run("update.sh")
    assert result.returncode == 2
    assert "changes of its own" in result.stderr and "Nothing was changed" in result.stderr
    assert checkout.head() == before != newest  # not pulled
    assert "# my own edit" in tracked.read_text()  # not touched
    assert not (checkout.docker / ".env").exists()  # not recorded
    assert checkout.builds() == []
    assert checkout.index("git pull") is None


def test_a_diverged_checkout_is_refused_with_nothing_changed(checkout):
    checkout.push_from_elsewhere("Origin moved on")
    mine = checkout.commit_here("I moved on too")
    result = checkout.run("update.sh")
    assert result.returncode == 2
    assert "can't simply fast-forward" in result.stderr and "Nothing was changed" in result.stderr
    assert checkout.head() == mine  # no merge commit, no rebase
    assert checkout.git("rev-list", "--merges", "--count", "HEAD") == "0"
    assert not (checkout.docker / ".env").exists()
    assert checkout.builds() == []


def test_an_unreachable_origin_is_refused_the_same_plain_way(checkout):
    checkout.origin.rename(checkout.root / "gone.git")
    before = checkout.head()
    result = checkout.run("update.sh")
    assert result.returncode == 2
    assert "can't be reached" in result.stderr
    assert checkout.head() == before
    assert checkout.builds() == []


def test_without_docker_it_stops_before_pulling_anything(checkout):
    before = checkout.head()
    checkout.push_from_elsewhere()
    result = checkout.run("update.sh", with_docker=False)
    assert result.returncode == 2
    assert "docker is not installed" in result.stderr
    assert checkout.head() == before
    assert checkout.index("git pull") is None


def test_running_it_twice_changes_nothing_the_second_time(checkout):
    checkout.push_from_elsewhere()
    assert checkout.run("update.sh").returncode == 0
    first = (checkout.docker / ".env").read_text()
    head = checkout.head()
    assert checkout.run("update.sh").returncode == 0
    assert (checkout.docker / ".env").read_text() == first
    assert checkout.head() == head
    for name in ("MOR_COMMIT", "MOR_COMMIT_DATE", "MOR_BRANCH", "MOR_REPO"):
        assert first.count(f"{name}=") == 1
    assert len(checkout.builds()) == 2  # docker's own cache makes the second one quick; the script doesn't second-guess it


def test_lines_that_are_already_right_are_not_touched_so_nothing_moves(checkout):
    assert checkout.run("update.sh", "--record-only").returncode == 0
    with (checkout.docker / ".env").open("a") as env:
        env.write("MOR_API_PORT=8001\n")  # added by hand after the recorded lines
    before = (checkout.docker / ".env").read_text()
    assert checkout.run("update.sh", "--record-only").returncode == 0
    assert (checkout.docker / ".env").read_text() == before


def test_an_older_recorded_commit_is_replaced_not_added_to_and_other_settings_stay(checkout):
    (checkout.docker / ".env").write_text("MOR_DATA_DIR=/srv/mor\nMOR_COMMIT=" + "0" * 40 + "\nMOR_API_PORT=8001\n")
    (checkout.docker / ".env").chmod(0o640)
    checkout.run("update.sh")
    text = (checkout.docker / ".env").read_text()
    assert text.count("MOR_COMMIT=") == 1 and checkout.env_value("MOR_COMMIT") == checkout.head()
    assert "MOR_DATA_DIR=/srv/mor\n" in text and "MOR_API_PORT=8001\n" in text
    assert stat.S_IMODE((checkout.docker / ".env").stat().st_mode) == 0o640  # rewritten in place, permissions kept


def test_record_only_writes_the_lines_and_pulls_and_builds_nothing(checkout):
    newest = checkout.push_from_elsewhere()
    result = checkout.run("update.sh", "--record-only", with_docker=False)
    assert result.returncode == 0, result.stderr
    assert checkout.env_value("MOR_COMMIT") == checkout.head() != newest
    assert checkout.index("git pull") is None and checkout.builds() == []


def test_a_folder_that_is_not_a_git_checkout_is_said_plainly(tmp_path, checkout):
    bare = tmp_path / "unpacked"
    bare.mkdir()
    (bare / "update.sh").write_text((DOCKER / "update.sh").read_text())
    result = subprocess.run(
        ["sh", str(bare / "update.sh")], cwd=bare, env={**checkout.env, "PATH": f"{checkout.bin}:/usr/bin:/bin"}, capture_output=True, text=True, stdin=subprocess.DEVNULL
    )
    assert result.returncode == 2
    assert "not a git checkout" in result.stderr
    assert not (bare / ".env").exists()


def test_the_optional_containers_you_have_are_rebuilt_with_the_rest(checkout):
    checkout.ps_services.write_text("core\napi\nallstar\nwinlink\ndirewolf\n")
    checkout.run("update.sh")
    (build,) = checkout.builds()
    assert build == "docker compose --profile allstar --profile winlink up -d --build"


def test_without_optional_containers_it_is_the_plain_command(checkout):
    checkout.ps_services.write_text("core\napi\nlogd\n")
    checkout.run("update.sh")
    assert checkout.builds() == ["docker compose up -d --build"]


def test_every_profile_in_compose_is_known_to_the_script():
    """update.sh maps a profiled service to its profile by name. A new profiled service in compose.yaml must be added there too."""
    services = yaml.safe_load((DOCKER / "compose.yaml").read_text())["services"]
    script = (DOCKER / "update.sh").read_text()
    for name, service in services.items():
        for profile in service.get("profiles", []):
            assert re.search(rf"\b{name}\b[^\n]*\) profile={profile} ;;|\b{name}\|[^\n]*\) profile={profile} ;;|\|{name}\) profile={profile} ;;", script), f"{name} ({profile})"


def test_help_and_unknown_options():
    result = subprocess.run(["sh", str(DOCKER / "update.sh"), "--help"], capture_output=True, text=True)
    assert result.returncode == 0 and "./update.sh" in result.stdout and "--record-only" in result.stdout
    assert "set -eu" not in result.stdout  # the help is the header comment only
    assert subprocess.run(["sh", str(DOCKER / "update.sh"), "--bogus"], capture_output=True, text=True).returncode == 2


def test_the_script_is_executable_and_valid_shell():
    assert (DOCKER / "update.sh").stat().st_mode & stat.S_IXUSR
    assert subprocess.run(["sh", "-n", str(DOCKER / "update.sh")]).returncode == 0
