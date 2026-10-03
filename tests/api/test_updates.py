import base64
import io
import json
import subprocess
import urllib.error

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.auth import AuthSettings
from api.service import RepeaterService
from api.updates import (
    REQUEST_PICKUP_SECONDS,
    STALLED_AFTER_SECONDS,
    UpdateChecker,
    Updater,
    UpdaterSettings,
    channel_from_env,
    github_repo,
    installed_version,
    read_log,
    updater_settings_from_env,
)
from api.users import UserStore

CURRENT = "a" * 40
LATEST = "b" * 40


def commit(sha, subject, date="2026-09-30T12:00:00Z"):
    return {"sha": sha, "commit": {"message": f"{subject}\n\nbody", "committer": {"date": date}}}


def http_error(code):
    return urllib.error.HTTPError("https://api.github.com", code, "error", {}, io.BytesIO(b""))


class FakeGitHub:
    def __init__(self, heads, comparisons=None, error=None, any_comparison=None):
        self.heads = heads
        self.comparisons = comparisons or {}
        self.error = error
        self.any_comparison = any_comparison
        self.urls = []

    def __call__(self, url):
        self.urls.append(url)
        if self.error is not None:
            raise self.error
        if "/commits/" in url:
            branch = url.rsplit("/", 1)[1]
            if branch not in self.heads:
                raise http_error(422)
            return self.heads[branch]
        return self.comparisons.get(url.rsplit("/", 1)[1], self.any_comparison)


def test_settings_come_from_the_service_environment(tmp_path):
    assert updater_settings_from_env({}) is None
    assert updater_settings_from_env({"MOREOPENREPEATER_UPDATER": str(tmp_path)}) is None
    settings = updater_settings_from_env(
        {"MOREOPENREPEATER_UPDATER": str(tmp_path), "MOREOPENREPEATER_UPDATE_REQUEST": str(tmp_path / "req")}
    )
    assert settings == UpdaterSettings(tmp_path, tmp_path / "req")


def test_channel_defaults_to_stable():
    assert channel_from_env({}) == "stable"
    assert channel_from_env({"MOREOPENREPEATER_UPDATE_CHANNEL": "beta"}) == "beta"
    assert channel_from_env({"MOREOPENREPEATER_UPDATE_CHANNEL": "nightly"}) == "stable"


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/gmisner/moreopenrepeater.git",
        "https://github.com/gmisner/moreopenrepeater",
        "git@github.com:gmisner/moreopenrepeater.git",
        "ssh://git@github.com/gmisner/moreopenrepeater.git",
    ],
)
def test_github_repo_from_remote_urls(url):
    assert github_repo(url) == "gmisner/moreopenrepeater"


def test_non_github_remotes_have_no_repo():
    assert github_repo("/tmp/src") is None
    assert github_repo("https://gitlab.com/someone/moreopenrepeater.git") is None


def test_installed_version_reads_the_checkout(tmp_path):
    def git(*args):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)

    git("init", "-q")
    (tmp_path / "f").write_text("x")
    git("add", "f")
    git("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "First commit")
    version = installed_version(tmp_path)
    assert version["subject"] == "First commit"
    assert len(version["sha"]) == 40
    assert installed_version(tmp_path / "missing") is None


def test_check_lists_new_commits_newest_first():
    github = FakeGitHub(
        {"main": commit(LATEST, "Third")},
        {f"{CURRENT}...{LATEST}": {"status": "ahead", "ahead_by": 2, "commits": [commit("c" * 40, "Second"), commit(LATEST, "Third")]}},
    )
    result = UpdateChecker("o/r", fetch=github).check("dev", CURRENT)
    assert result["relation"] == "ahead"
    assert result["latest"]["subject"] == "Third"
    assert [c["subject"] for c in result["new_commits"]] == ["Third", "Second"]
    assert result["new_commit_count"] == 2
    assert result["error"] is None
    assert github.urls == ["https://api.github.com/repos/o/r/commits/main", f"https://api.github.com/repos/o/r/compare/{CURRENT}...{LATEST}"]


def test_check_when_already_on_the_channels_commit():
    github = FakeGitHub({"stable": commit(CURRENT, "Installed")})
    result = UpdateChecker("o/r", fetch=github).check("stable", CURRENT)
    assert result["relation"] == "identical"
    assert result["new_commits"] == []
    assert len(github.urls) == 1


def test_check_reports_an_older_channel():
    github = FakeGitHub({"stable": commit(LATEST, "Old")}, {f"{CURRENT}...{LATEST}": {"status": "behind", "ahead_by": 0, "commits": []}})
    assert UpdateChecker("o/r", fetch=github).check("stable", CURRENT)["relation"] == "behind"


def test_check_explains_a_missing_branch_and_a_rate_limit():
    assert "no beta branch" in UpdateChecker("o/r", fetch=FakeGitHub({})).check("beta", CURRENT)["error"]
    limited = UpdateChecker("o/r", fetch=FakeGitHub({}, error=http_error(403)))
    assert "rate limit" in limited.check("beta", CURRENT)["error"]
    offline = UpdateChecker("o/r", fetch=FakeGitHub({}, error=urllib.error.URLError("no route")))
    assert offline.check("beta", CURRENT)["error"] == "Couldn't reach GitHub: no route"


def test_check_without_a_github_checkout():
    assert "wasn't installed from GitHub" in UpdateChecker(None).check("stable", CURRENT)["error"]


def test_check_results_are_cached_but_errors_are_not():
    now = [0.0]
    github = FakeGitHub({"stable": commit(CURRENT, "Installed")})
    checker = UpdateChecker("o/r", fetch=github, clock=lambda: now[0])
    checker.check("stable", CURRENT)
    checker.check("stable", CURRENT)
    assert len(github.urls) == 1
    checker.check("stable", CURRENT, refresh=True)
    assert len(github.urls) == 2
    now[0] = 1000.0
    checker.check("stable", CURRENT)
    assert len(github.urls) == 3

    failing = FakeGitHub({}, error=urllib.error.URLError("down"))
    checker = UpdateChecker("o/r", fetch=failing)
    checker.check("stable", CURRENT)
    checker.check("stable", CURRENT)
    assert len(failing.urls) == 2


def write_status(settings, **status):
    (settings.status_dir / "status.json").write_text(json.dumps(status))


def make_updater(tmp_path, start=1000.0):
    now = [start]
    settings = UpdaterSettings(tmp_path, tmp_path / "data" / "update-request")
    (tmp_path / "data").mkdir()
    return Updater(settings, clock=lambda: now[0]), settings, now


def test_request_writes_the_channel_and_waits_for_the_updater(tmp_path):
    updater, settings, now = make_updater(tmp_path)
    write_status(settings, state="succeeded", channel="stable", started_at=10.0)
    updater.request("beta")
    assert settings.request_path.read_text() == "beta\n"
    assert not settings.request_path.with_name("update-request.tmp").exists()
    assert updater.status()["state"] == "requested"
    assert updater.busy()

    now[0] += 1
    write_status(settings, state="running", channel="beta", started_at=now[0])
    assert updater.status()["state"] == "running"
    write_status(settings, state="succeeded", channel="beta", started_at=now[0], to_sha=LATEST)
    assert updater.status()["state"] == "succeeded"
    assert not updater.busy()


def test_a_run_stamped_in_whole_seconds_still_answers_the_request(tmp_path):
    updater, settings, now = make_updater(tmp_path, start=1000.4)
    write_status(settings, state="succeeded", channel="stable", started_at=900.0)
    updater.request("stable")
    write_status(settings, state="running", channel="stable", started_at=1000.0)
    now[0] += REQUEST_PICKUP_SECONDS + 1
    assert updater.status()["state"] == "running"


def test_a_request_nobody_picks_up_turns_into_a_failure(tmp_path):
    updater, _settings, now = make_updater(tmp_path)
    updater.request("dev")
    now[0] += REQUEST_PICKUP_SECONDS + 1
    status = updater.status()
    assert status["state"] == "failed"
    assert "moreopenrepeater-update.path" in status["message"]


def test_an_update_running_for_too_long_is_reported_as_failed(tmp_path):
    updater, settings, now = make_updater(tmp_path)
    write_status(settings, state="running", channel="dev", started_at=now[0])
    assert updater.busy()
    now[0] += STALLED_AFTER_SECONDS + 1
    assert updater.status()["state"] == "failed"
    assert not updater.busy()


def test_request_refuses_unknown_channels_and_missing_updater(tmp_path):
    updater, _settings, _now = make_updater(tmp_path)
    with pytest.raises(ValueError):
        updater.request("nightly")
    with pytest.raises(RuntimeError):
        Updater(None).request("stable")
    assert Updater(None).status() is None


def test_unreadable_status_is_treated_as_none(tmp_path):
    updater, settings, _now = make_updater(tmp_path)
    (settings.status_dir / "status.json").write_text("{not json")
    assert updater.status() is None


def test_read_log_returns_the_tail(tmp_path):
    settings = UpdaterSettings(tmp_path, tmp_path / "req")
    assert read_log(settings) == []
    (tmp_path / "update.log").write_text("\n".join(f"line {i}" for i in range(10)))
    assert read_log(settings, lines=3) == ["line 7", "line 8", "line 9"]


def basic(username, password):
    return {"Authorization": "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()}


def make_client(tmp_path, updater=None, users=None):
    github = FakeGitHub(
        {"main": commit(LATEST, "Newest")}, any_comparison={"status": "ahead", "ahead_by": 1, "commits": [commit(LATEST, "Newest")]}
    )
    app = create_app(
        service=RepeaterService(),
        start_background_tick=False,
        log_path=tmp_path / "t.log",
        auth_settings=AuthSettings("admin", "hunter2"),
        users=users if users is not None else UserStore(),
        updater=updater if updater is not None else Updater(None),
        update_checker=UpdateChecker("o/r", fetch=github),
        update_channel="beta",
    )
    return TestClient(app)


def test_updates_route_reports_the_install(tmp_path):
    client = make_client(tmp_path)
    body = client.get("/api/updates", headers=basic("admin", "hunter2")).json()
    assert body["available"] is False
    assert body["channel"] == "beta"
    assert body["status"] is None
    assert len(body["version"]["sha"]) == 40  # this repo's checkout


def test_starting_an_update_writes_the_request(tmp_path):
    updater, settings, _now = make_updater(tmp_path)
    client = make_client(tmp_path, updater=updater)
    response = client.post("/api/updates", json={"channel": "dev"}, headers=basic("admin", "hunter2"))
    assert response.status_code == 200
    assert response.json()["status"]["state"] == "requested"
    assert settings.request_path.read_text() == "dev\n"

    again = client.post("/api/updates", json={"channel": "dev"}, headers=basic("admin", "hunter2"))
    assert again.status_code == 409
    assert "already in progress" in again.json()["detail"]


def test_update_needs_the_updater_and_a_known_channel(tmp_path):
    client = make_client(tmp_path)
    response = client.post("/api/updates", json={"channel": "dev"}, headers=basic("admin", "hunter2"))
    assert response.status_code == 409
    assert "install-pi.sh" in response.json()["detail"]
    assert client.post("/api/updates", json={"channel": "nightly"}, headers=basic("admin", "hunter2")).status_code == 422


def test_only_admins_see_or_start_updates(tmp_path):
    users = UserStore()
    users.add("alice", "password1", "admin")
    users.add("olive", "password2", "operator")
    client = make_client(tmp_path, users=users)
    assert client.get("/api/updates", headers=basic("olive", "password2")).status_code == 403
    assert client.post("/api/updates", json={"channel": "dev"}, headers=basic("olive", "password2")).status_code == 403
    assert client.get("/api/updates/check?channel=dev", headers=basic("olive", "password2")).status_code == 403


def test_check_route_asks_github(tmp_path):
    client = make_client(tmp_path)
    body = client.get("/api/updates/check?channel=dev", headers=basic("admin", "hunter2")).json()
    assert body["branch"] == "main"
    assert body["latest"]["subject"] == "Newest"
    assert body["relation"] == "ahead"
    assert [c["subject"] for c in body["new_commits"]] == ["Newest"]


def test_log_route_tails_the_updater_log(tmp_path):
    updater, settings, _now = make_updater(tmp_path)
    (settings.status_dir / "update.log").write_text("==> Fetching\n==> Installing\n")
    client = make_client(tmp_path, updater=updater)
    assert client.get("/api/updates/log", headers=basic("admin", "hunter2")).json() == ["==> Fetching", "==> Installing"]


def test_log_route_is_empty_without_the_updater(tmp_path):
    client = make_client(tmp_path)
    assert client.get("/api/updates/log", headers=basic("admin", "hunter2")).json() == []
