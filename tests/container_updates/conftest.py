"""A git checkout with an origin, and `git` and `docker` stood in by shims, for docker/update.sh and setup.sh.

`git` is a real git (a throwaway origin and clone in tmp_path) behind a shim that writes down each call, so the tests
can say what ran and in what order. `docker` is a small shim that writes down each call and, when asked to build, what
.env held at that moment: that is how a test knows the commit was recorded before the build."""
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

DOCKER = Path(__file__).resolve().parents[2] / "docker"
REPO_URL = "https://github.com/gmisner/moreopenrepeater.git"
REAL_GIT = shutil.which("git")


class Checkout:
    def __init__(self, root: Path):
        self.root = root
        self.origin = root / "origin.git"
        self.work = root / "work"
        self.docker = self.work / "docker"
        self.other = root / "other"
        self.bin = root / "bin"
        self.calls = root / "calls"
        self.ps_services = root / "ps-services"
        self.env = {
            "HOME": str(root),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
            "GIT_TERMINAL_PROMPT": "0",
        }

    # --- real git, for setting the scene (not through the shim) ---
    def git(self, *args, cwd=None):
        result = subprocess.run([REAL_GIT, *args], cwd=cwd or self.work, env={**self.env, "PATH": "/usr/bin:/bin"}, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    def head(self, cwd=None):
        return self.git("rev-parse", "HEAD", cwd=cwd)

    def push_from_elsewhere(self, subject="A newer commit", files=None):
        """Someone else's commit lands on origin's main."""
        self.git("pull", "-q", "--ff-only", cwd=self.other)
        for name, text in (files or {f"change-{subject}.txt": subject}).items():
            (self.other / name).parent.mkdir(parents=True, exist_ok=True)
            (self.other / name).write_text(text)
        self.git("add", "-A", cwd=self.other)
        self.git("commit", "-qm", subject, cwd=self.other)
        self.git("push", "-q", "origin", "main", cwd=self.other)
        return self.head(cwd=self.other)

    def commit_here(self, subject="A local commit"):
        (self.work / f"local-{subject}.txt").write_text(subject)
        self.git("add", "-A")
        self.git("commit", "-qm", subject)
        return self.head()

    # --- what the shims saw ---
    def log(self):
        return self.calls.read_text().splitlines() if self.calls.exists() else []

    def index(self, prefix):
        hits = [i for i, line in enumerate(self.log()) if line.startswith(prefix)]
        return hits[0] if hits else None

    def compose_calls(self):
        return [line for line in self.log() if line.startswith("docker compose")]

    def builds(self):
        return [line for line in self.log() if line.startswith("docker compose") and "up -d --build" in line]

    def env_value(self, name):
        path = self.docker / ".env"
        if not path.exists():
            return None
        for line in path.read_text().splitlines():
            if line.startswith(f"{name}="):
                return line.split("=", 1)[1]
        return None

    def run(self, script, *args, with_docker=True, **env):
        # Only the shims and a private folder of the system's tools: a real docker on the machine running the tests (CI has
        # one) must not be found, whether or not the docker shim is there.
        lean = self.root / "lean"
        lean.mkdir(exist_ok=True)
        for tool in ("sh", "sed", "grep", "tr", "head", "cp", "chmod", "mkdir", "stat", "id", "mv", "cat", "printf", "awk", "tail",
                     "dirname", "sleep", "date", "rm", "ls", "tee", "basename", "cut", "sort", "uniq", "wc", "touch"):
            found = shutil.which(tool, path="/usr/bin:/bin")
            if found and not (lean / tool).exists():
                (lean / tool).symlink_to(found)
        path = f"{self.bin}:{lean}"
        if not with_docker:
            (self.bin / "docker").unlink(missing_ok=True)
        return subprocess.run(
            ["sh", str(self.docker / script), *args], cwd=self.docker, env={**self.env, "PATH": path, "MOR_SETUP_CHOWN": "0", "MOR_SETUP_WAIT": "5", **env},
            capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL,
        )


@pytest.fixture
def checkout(tmp_path):
    c = Checkout(tmp_path)
    c.git("init", "-q", "--bare", "-b", "main", str(c.origin), cwd=tmp_path)
    c.git("clone", "-q", str(c.origin), str(c.work), cwd=tmp_path)
    c.git("config", "pull.rebase", "false")  # so a pull without --ff-only would quietly merge a diverged branch
    shutil.copytree(DOCKER, c.docker, ignore=shutil.ignore_patterns("__pycache__", ".env"))
    c.git("add", "-A")
    c.git("commit", "-qm", "The first commit")
    c.git("branch", "-M", "main")
    c.git("push", "-q", "-u", "origin", "main")
    # The remote looks like GitHub (what the dashboard checks against) but fetches from the throwaway origin.
    c.git("config", "remote.origin.url", REPO_URL)
    c.git("config", f"url.{c.origin}.insteadOf", REPO_URL)
    c.git("clone", "-q", str(c.origin), str(c.other), cwd=tmp_path)

    c.bin.mkdir()

    def shim(name, body):
        target = c.bin / name
        target.write_text("#!/bin/sh\n" + body)
        target.chmod(target.stat().st_mode | stat.S_IXUSR)

    shim("git", f'echo "git $*" >> {c.calls}\nexec {REAL_GIT} "$@"')
    shim("docker", f'''echo "docker $*" >> {c.calls}
case "$*" in
  "compose version") echo "Docker Compose version v2.26.1"; exit 0 ;;
  *"up -d --build"*) echo "env-at-build: $(grep -h '^MOR_' .env 2>/dev/null | tr '\\n' ' ')" >> {c.calls} ;;
esac
case "$1" in
  ps) cat {c.ps_services} 2>/dev/null || true ;;
esac
case "$*" in
  *"config --services"*)
    printf 'core\\napi\\nlogd\\npiper\\n'
    case "$*" in *"--profile allstar"*) echo allstar ;; esac
    case "$*" in *"--profile time"*) echo chrony ;; esac ;;
  *" pull"*) [ ! -e {c.root}/pull-fails ] || {{ echo "manifest unknown" >&2; exit 1; }} ;;
esac
case "$*" in
  *"ps --format"*) printf 'moreopenrepeater-core-1 Up 5 seconds (healthy)\\nmoreopenrepeater-api-1 Up 5 seconds (healthy)\\n' ;;
esac
exit 0''')
    shim("systemctl", "echo inactive; exit 3")
    shim("sudo", f'echo "sudo $*" >> {c.calls}; exit 0')
    shim("ip", "exit 0")
    shim("hostname", "echo 192.168.1.50")
    shim("tailscale", "exit 0")
    return c
