"""Which version is installed, what each release channel offers, and asking
the updater to install one.

Channels are branches: dev is `main` (every push), beta and stable are
`beta` and `stable`, which only move when a commit is promoted (see the
Promote workflow). The dashboard can't update itself -- the code is owned by
root and the service runs as an unprivileged user -- so it drops the chosen
channel into a request file, and a root-run systemd unit
(moreopenrepeater-update.path / .service, scripts/update.sh) runs the
installer for that channel, rolling back if the new version doesn't come
up. That unit reports progress in a status file and log the service can read.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

CHANNEL_BRANCHES = {"stable": "stable", "beta": "beta", "dev": "main"}
DEFAULT_CHANNEL = "stable"
CHECK_CACHE_SECONDS = 300
MAX_LISTED_COMMITS = 30
# The updater normally picks a request up within a second; after this long
# without it, something's wrong with the units.
REQUEST_PICKUP_SECONDS = 60
STALLED_AFTER_SECONDS = 45 * 60
MAX_LOG_LINES = 400


@dataclass(frozen=True)
class UpdaterSettings:
    status_dir: Path  # status.json and update.log, written by the updater
    request_path: Path


def updater_settings_from_env(env: dict) -> Optional[UpdaterSettings]:
    """None unless installed by install-pi.sh, which sets both in the unit."""
    status_dir = env.get("MOREOPENREPEATER_UPDATER")
    request_path = env.get("MOREOPENREPEATER_UPDATE_REQUEST")
    if not status_dir or not request_path:
        return None
    return UpdaterSettings(Path(status_dir), Path(request_path))


def channel_from_env(env: dict) -> str:
    channel = env.get("MOREOPENREPEATER_UPDATE_CHANNEL", "")
    return channel if channel in CHANNEL_BRANCHES else DEFAULT_CHANNEL


def _git(repo: Path, *args: str) -> Optional[str]:
    # The checkout belongs to root; reading it as the service user needs
    # safe.directory, which git only honours from the command line or
    # system/global config.
    try:
        result = subprocess.run(
            ["git", "-c", "safe.directory=*", "-C", str(repo), *args],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def installed_version(repo: Path) -> Optional[dict]:
    out = _git(repo, "log", "-1", "--format=%H%n%cI%n%s")
    if not out:
        return None
    sha, date, subject = (out.split("\n", 2) + ["", ""])[:3]
    return {"sha": sha, "date": date, "subject": subject}


def github_repo(url: str) -> Optional[str]:
    """`owner/name` for a GitHub remote URL (https or ssh), else None."""
    match = re.fullmatch(r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)([\w.-]+/[\w.-]+?)(?:\.git)?/?", url.strip())
    return match.group(1) if match else None


def repo_from_checkout(repo: Path) -> Optional[str]:
    url = _git(repo, "config", "--get", "remote.origin.url")
    return github_repo(url) if url else None


def _fetch_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "moreopenrepeater", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read(5_000_000))


def _commit_summary(commit: dict) -> dict:
    details = commit.get("commit", {})
    return {
        "sha": commit.get("sha", ""),
        "date": details.get("committer", {}).get("date", ""),
        "subject": details.get("message", "").split("\n", 1)[0],
    }


class UpdateChecker:
    """Asks GitHub what a channel's branch holds compared with the installed
    commit. Results are cached for a few minutes: unauthenticated GitHub API
    calls are limited to 60 an hour."""

    def __init__(self, repo: Optional[str], fetch: Callable[[str], dict] = _fetch_json, clock: Callable[[], float] = time.monotonic) -> None:
        self.repo = repo
        self._fetch = fetch
        self._clock = clock
        self._cache: dict[tuple[str, str], tuple[float, dict]] = {}

    def check(self, channel: str, current_sha: str, *, refresh: bool = False) -> dict:
        key = (channel, current_sha)
        cached = self._cache.get(key)
        if cached and not refresh and self._clock() - cached[0] < CHECK_CACHE_SECONDS:
            return cached[1]
        result = self._check(channel, current_sha)
        if result.get("error") is None:
            self._cache[key] = (self._clock(), result)
        return result

    def _check(self, channel: str, current_sha: str) -> dict:
        branch = CHANNEL_BRANCHES[channel]
        result: dict = {"channel": channel, "branch": branch, "latest": None, "relation": None, "new_commits": [], "error": None}
        if self.repo is None:
            result["error"] = "This copy wasn't installed from GitHub, so there's nothing to check against."
            return result
        base = f"https://api.github.com/repos/{self.repo}"
        try:
            result["latest"] = _commit_summary(self._fetch(f"{base}/commits/{branch}"))
            if result["latest"]["sha"] == current_sha:
                result["relation"] = "identical"
                return result
            comparison = self._fetch(f"{base}/compare/{current_sha}...{result['latest']['sha']}")
        except urllib.error.HTTPError as error:
            if error.code in (404, 422):  # 422: no such branch
                result["error"] = f"GitHub has no {branch} branch, or doesn't know the installed commit."
            elif error.code in (403, 429):
                result["error"] = "GitHub's rate limit was reached; try again in a while."
            else:
                result["error"] = f"GitHub answered {error.code}."
            return result
        except (OSError, ValueError) as error:
            result["error"] = f"Couldn't reach GitHub: {getattr(error, 'reason', error)}"
            return result
        result["relation"] = comparison.get("status")
        commits = [_commit_summary(c) for c in comparison.get("commits", [])]
        result["new_commits"] = list(reversed(commits))[:MAX_LISTED_COMMITS]
        result["new_commit_count"] = comparison.get("ahead_by", len(commits))
        return result


def read_status(settings: UpdaterSettings) -> Optional[dict]:
    try:
        status = json.loads((settings.status_dir / "status.json").read_text())
    except (OSError, ValueError):
        return None
    return status if isinstance(status, dict) else None


def read_log(settings: UpdaterSettings, lines: int = MAX_LOG_LINES) -> list[str]:
    try:
        text = (settings.status_dir / "update.log").read_text(errors="replace")
    except OSError:
        return []
    return text.splitlines()[-lines:]


class Updater:
    def __init__(self, settings: Optional[UpdaterSettings], clock: Callable[[], float] = time.time) -> None:
        self.settings = settings
        self._clock = clock
        self._requested_at: Optional[float] = None

    @property
    def available(self) -> bool:
        return self.settings is not None

    def status(self) -> Optional[dict]:
        """The updater's last report, or a "requested" placeholder between
        the request and the updater picking it up."""
        if self.settings is None:
            return None
        status = read_status(self.settings)
        now = self._clock()
        if self._requested_at is not None and (status is None or status.get("started_at", 0) < self._requested_at):
            if now - self._requested_at < REQUEST_PICKUP_SECONDS:
                return {"state": "requested", "started_at": self._requested_at}
            return {"state": "failed", "started_at": self._requested_at, "message": "The updater didn't pick up the request. Is moreopenrepeater-update.path enabled?"}
        if status and status.get("state") == "running" and now - status.get("started_at", now) > STALLED_AFTER_SECONDS:
            return {**status, "state": "failed", "message": "The update has been running for too long; see the log."}
        return status

    def busy(self) -> bool:
        status = self.status()
        return status is not None and status.get("state") in ("requested", "running")

    def request(self, channel: str) -> None:
        if self.settings is None:
            raise RuntimeError("updates aren't set up on this install")
        if channel not in CHANNEL_BRANCHES:
            raise ValueError(f"unknown channel {channel!r}")
        path = self.settings.request_path
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(channel + "\n")
        os.replace(tmp, path)  # the path unit must never see a half-written file
        self._requested_at = self._clock()
