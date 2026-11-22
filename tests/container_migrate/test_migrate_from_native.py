"""docker/migrate-from-native.sh: moving a native install into the container install (#233).

Runs the real script against a fake native install built in a temp dir. Nothing touches the real system: the script's
own hooks (MOR_MIGRATE_ETC, MOR_MIGRATE_OPT, MOR_MIGRATE_CHOWN and friends) point it at fake /etc and /opt folders, and
`docker`, `systemctl`, `sudo` and `pgrep` are small shims that write down what they were asked. What these prove is what
the script does with files and commands. They do not prove anything about a Pi, real Docker, hardware or the air."""
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path

import pytest

DOCKER = Path(__file__).resolve().parents[2] / "docker"

AUTH_PASSWORD = "Correct-Horse-9-Battery"
AMI_SECRET = "ami-s3cret-value"
HA_TOKEN = "ha-token-abcdef123456"
UNKNOWN_SECRET = "unknown-key-secret-7788"
SECRETS = [AUTH_PASSWORD, AMI_SECRET, HA_TOKEN, UNKNOWN_SECRET]

NATIVE_ENV = f"""# the native install's settings
MOREOPENREPEATER_HOST=0.0.0.0
MOREOPENREPEATER_PORT=8080
MOREOPENREPEATER_AUTH_USER=admin
MOREOPENREPEATER_AUTH_PASSWORD={AUTH_PASSWORD}
MOREOPENREPEATER_UPDATE_CHANNEL=stable
MOREOPENREPEATER_PROTECT_SD=1
MOREOPENREPEATER_STALL_SECONDS=15
MOREOPENREPEATER_AMI_HOST=127.0.0.1
MOREOPENREPEATER_AMI_PORT=5038
MOREOPENREPEATER_AMI_USER=moreopenrepeater
MOREOPENREPEATER_AMI_SECRET={AMI_SECRET}
MOREOPENREPEATER_HOMEASSISTANT_TOKEN={HA_TOKEN}
MOREOPENREPEATER_CM108_HIDRAW=auto
PIPER_VOICE=en_US-amy-low
MOREOPENREPEATER_SOMETHING_NEW={UNKNOWN_SECRET}
"""


def digest_tree(root: Path) -> dict:
    """Every file under root: relative path -> (sha256, mode). Folders are listed by their mode too."""
    seen = {}
    for path in sorted(root.rglob("*")):
        rel = str(path.relative_to(root))
        if rel == "calls":  # the shims' own log of what they were asked
            continue
        mode = stat.S_IMODE(path.lstat().st_mode)
        if path.is_symlink():
            seen[rel] = ("link", os.readlink(path), mode)
        elif path.is_file():
            seen[rel] = (hashlib.sha256(path.read_bytes()).hexdigest(), mode)
        else:
            seen[rel] = ("dir", mode)
    return seen


class Site:
    """A fake native install, a copy of the docker folder to migrate into, and the shims."""

    def __init__(self, root: Path):
        self.root = root
        self.etc = root / "etc"
        self.opt = root / "opt"
        self.native = self.opt / "moreopenrepeater"
        self.data = self.native / "data"
        self.docker = root / "docker"
        self.target = root / "container-data"
        self.bin = root / "bin"
        self.state = root / "units"
        self.calls = root / "calls"
        self.dev = root / "dev"
        self.ts = root / "tailscale"
        self._build()

    # ----- the fake machine -----
    def _build(self):
        for folder in (self.etc / "moreopenrepeater", self.etc / "systemd/system", self.etc / "udev/rules.d", self.etc / "asterisk",
                       self.etc / "systemd/journald.conf.d", self.data / "audio", self.data / "recordings", self.data / "piper",
                       self.data / "backups", self.bin, self.state, self.dev, self.ts):
            folder.mkdir(parents=True, exist_ok=True)
        (self.etc / "moreopenrepeater/env").write_text(NATIVE_ENV)
        (self.etc / "systemd/system/moreopenrepeater.service").write_text("[Service]\n")
        (self.etc / "udev/rules.d/99-cm108.rules").write_text("# native\n")
        (self.etc / "asterisk/rpt.conf").write_text("[99001]\n")
        (self.etc / "systemd/journald.conf.d/60-moreopenrepeater.conf").write_text("[Journal]\nStorage=volatile\n")
        (self.state / "moreopenrepeater.active").write_text("active\n")
        (self.state / "moreopenrepeater.enabled").write_text("enabled\n")
        (self.ts / "tailscaled.sock").write_text("")

        (self.data / "state.json").write_text(json.dumps({"config": {"callsign": "W1AW", "tot_duration": 120}, "macros": [], "announcements": []}))
        (self.data / "users.json").write_text(json.dumps({"users": [{"name": "op", "hash": "pbkdf2$abc"}]}))
        (self.data / "hub-sites.json").write_text(json.dumps({"sites": [{"id": "a", "api_token": "site-token"}]}))
        (self.data / "audio/index.json").write_text("[]")
        (self.data / "audio/clip1.wav").write_bytes(b"RIFF" + b"\x01" * 64)
        (self.data / "recordings/1700000000000.wav").write_bytes(b"RIFF" + b"\x02" * 64)
        (self.data / "piper/voice.onnx").write_bytes(b"\x00" * 16)
        (self.data / "backups/old.zip").write_bytes(b"PK")
        (self.data / "run-state.json").write_text("{}")
        self._database("activity.db", 5)
        self._database("audit.db", 2)
        for name in (self.data / "state.json", self.data / "users.json", self.data / "hub-sites.json"):
            name.chmod(0o600)

        shutil.copytree(DOCKER, self.docker, ignore=shutil.ignore_patterns("__pycache__"))
        (self.root / "ptt").write_text("")
        (self.docker / ".env").write_text(f"MOR_PTT_HIDRAW={self.root / 'ptt'}\nMOR_DATA_DIR={self.target}\n")
        self._shims()

    def _database(self, name: str, rows: int):
        """A SQLite database in WAL mode whose rows are only in the -wal file, the way a stopped-by-power-cut one is."""
        live = self.root / "live-db" / name
        live.parent.mkdir(exist_ok=True)
        conn = sqlite3.connect(live)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA wal_autocheckpoint=0")
        conn.execute("CREATE TABLE event (id INTEGER PRIMARY KEY, note TEXT)")
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        for n in range(rows):
            conn.execute("INSERT INTO event (note) VALUES (?)", (f"row {n}",))
            conn.commit()
        wal = Path(str(live) + "-wal")
        assert wal.exists() and wal.stat().st_size > 0, "the fixture should leave rows in the -wal file"
        shutil.copy(live, self.data / name)
        shutil.copy(wal, self.data / (name + "-wal"))
        shutil.copy(str(live) + "-shm", self.data / (name + "-shm"))
        conn.close()

    def _shim(self, name: str, body: str):
        path = self.bin / name
        path.write_text("#!/bin/sh\n" + body)
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    def _shims(self):
        calls, state = self.calls, self.state
        self._shim("docker", f'''echo "docker $*" >> {calls}
case "$*" in
  "compose version") echo "Docker Compose version v2.26.1" ;;
  "compose ps -q") [ ! -f {self.root}/stack-running ] || cat {self.root}/stack-running; [ ! -f {self.root}/ps-fails ] || exit 1 ;;
  *"ps --format"*) printf 'moreopenrepeater-api-1 Up 5 seconds (healthy)\\n' ;;
esac
exit 0''')
        self._shim("systemctl", f'''echo "systemctl $*" >> {calls}
case "$1" in
  is-active) cat {state}/"$2".active 2>/dev/null || {{ echo inactive; exit 3; }} ;;
  is-enabled) cat {state}/"$2".enabled 2>/dev/null || {{ echo disabled; exit 1; }} ;;
  disable) echo inactive > {state}/"$3".active; echo disabled > {state}/"$3".enabled ;;
  enable) echo active > {state}/"$3".active; echo enabled > {state}/"$3".enabled ;;
esac
exit 0''')
        # sudo is only let through for the commands the script reads and stops things with; udev and cp stay pretend
        self._shim("sudo", f'''echo "sudo $*" >> {calls}
case "$1" in
  cat|systemctl|*python*) exec "$@" ;;
esac
exit 0''')
        self._shim("pgrep", "exit 1")
        self._shim("tailscale", "exit 0")

    # ----- running -----
    def env(self, **extra) -> dict:
        return {
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "HOME": str(self.root),
            "MOR_MIGRATE_ETC": str(self.etc),
            "MOR_MIGRATE_OPT": str(self.opt),
            "MOR_MIGRATE_CHOWN": "0",
            "MOR_MIGRATE_PYTHON": sys.executable,
            "MOR_DEV_DIR": str(self.dev),
            "MOR_TAILSCALE_SOCKET_DIR": str(self.ts),
            "MOR_SETUP_CHOWN": "0",
            "MOR_SETUP_WAIT": "5",
            "MOR_TIMEZONE_FILE": str(self.root / "no-timezone"),
            "MOR_LOCALTIME_FILE": str(self.root / "no-localtime"),
            "MOR_KMSG_FILE": str(self.root / "no-kmsg"),
            **extra,
        }

    def run(self, *args, setup_args_file=None, **env):
        if setup_args_file is not None:
            fake = self.root / "fake-setup.sh"
            fake.write_text(f'echo "setup $*" >> {self.calls}\nexit 0\n')
            env["MOR_MIGRATE_SETUP"] = str(fake)
        return subprocess.run(["/bin/sh", str(self.docker / "migrate-from-native.sh"), *args], cwd=self.docker, env=self.env(**env),
                              capture_output=True, text=True, timeout=120)

    def call_log(self) -> list:
        return self.calls.read_text().splitlines() if self.calls.exists() else []

    def value(self, file: str, name: str):
        path = self.docker / file
        if not path.exists():
            return None
        found = None
        for line in path.read_text().splitlines():
            if line.startswith(name + "="):
                found = line[len(name) + 1:]
        return found


@pytest.fixture
def site(tmp_path):
    return Site(tmp_path)


def no_secrets(*results):
    for result in results:
        text = result.stdout + result.stderr
        for secret in SECRETS:
            assert secret not in text, f"a secret was printed: {secret[:6]}..."


# ---------------------------------------------------------------------------------------------------------------------
# the dry run

def test_a_dry_run_changes_nothing_and_says_what_it_would_do(site):
    everything_before = digest_tree(site.root)
    result = site.run()
    assert result.returncode == 0, result.stdout + result.stderr
    assert digest_tree(site.root) == everything_before  # not a file, folder or mode changed; nothing created
    assert not site.target.exists()
    assert not (site.docker / "api.env").exists()
    assert not any(re.search(r"systemctl (disable|enable)", call) for call in site.call_log())  # nothing was stopped
    out = result.stdout
    assert "DRY RUN" in out and "Nothing was changed" in out
    assert "--apply" in out
    assert "will copy    state.json" in out and "callsign W1AW" in out
    assert "will copy    activity.db" in out and "will copy    audio" in out
    assert "left behind  recordings" in out and "--recordings" in out
    assert "left behind  piper" in out and "left behind  backups" in out and "left behind  run-state.json" in out


def test_a_dry_run_prints_no_secret_and_says_only_whether_there_is_one(site):
    result = site.run()
    no_secrets(result)
    assert "MOREOPENREPEATER_AUTH_PASSWORD" in result.stdout and "(secret)" in result.stdout
    assert "would write  MOREOPENREPEATER_AMI_SECRET" not in result.stdout  # a loopback AMI is not carried at all


def test_it_names_what_it_found_on_the_machine(site):
    out = site.run().stdout
    assert "moreopenrepeater (active, enabled)" in out
    assert "99-cm108.rules" in out
    assert "AllStar node:    found" in out
    assert "SD protection:   on" in out
    assert "Tailscale:       found" in out
    assert "DMR bridge:      none found" in out
    (site.opt / "Analog_Bridge").mkdir()
    assert "DMR bridge:      found" in site.run().stdout


def test_it_says_plainly_that_dmr_has_no_importer_and_what_the_owner_does(site):
    (site.opt / "Analog_Bridge").mkdir()
    out = site.run().stdout
    assert "NO importer" in out and "./setup.sh --dmr" in out and "enter the callsign, DMR Id, network and password again" in out


def test_allstar_is_printed_as_the_next_commands_and_not_run(site):
    out = site.run().stdout
    assert "./setup.sh --import-allstar" in out and "sudo systemctl disable --now asterisk" in out
    assert "NOT touched" in out


# ---------------------------------------------------------------------------------------------------------------------
# refusals

def test_it_refuses_a_target_that_is_not_empty_and_changes_nothing(site):
    site.target.mkdir()
    (site.target / "state.json").write_text('{"config": {"callsign": "OLD"}}')
    before = digest_tree(site.root)
    dry = site.run()
    assert dry.returncode == 2 and "not empty" in dry.stdout
    applied = site.run("--apply")
    assert applied.returncode == 2 and "not empty" in applied.stderr and "--into-existing" in applied.stderr
    assert digest_tree(site.root) == before
    assert not any("disable" in call for call in site.call_log())  # the native service was left running


def test_it_refuses_when_the_native_data_has_no_state_json(site):
    (site.data / "state.json").unlink()
    result = site.run("--apply")
    assert result.returncode == 2 and "no state.json" in result.stderr
    assert not any("disable" in call for call in site.call_log())
    assert not site.target.exists()


def test_it_refuses_a_state_json_with_no_callsign(site):
    (site.data / "state.json").write_text('{"config": {}}')
    result = site.run("--apply")
    assert result.returncode == 2 and "no callsign" in result.stderr


def test_it_refuses_when_the_container_stack_is_running(site):
    (site.root / "stack-running").write_text("abc123\n")
    result = site.run("--apply")
    assert result.returncode == 2 and "already running" in result.stderr
    assert not any("disable" in call for call in site.call_log())
    assert not site.target.exists()


def test_it_refuses_when_docker_cannot_be_asked_about_the_stack(site):
    (site.root / "ps-fails").write_text("")
    result = site.run("--apply")
    assert result.returncode == 2 and "could not be asked" in result.stderr


def test_it_refuses_when_docker_is_missing(site, tmp_path):
    limited = tmp_path / "limited"
    limited.mkdir()
    for folder in ("/bin", "/usr/bin"):
        for entry in Path(folder).iterdir():
            link = limited / entry.name
            if entry.name != "docker" and not link.exists():
                link.symlink_to(entry)
    result = site.run("--apply", PATH=f"{site.bin}-nothing:{limited}")
    assert result.returncode == 2 and "docker is not installed" in result.stderr
    assert not site.target.exists()


def test_it_refuses_a_native_install_that_is_not_there(site):
    result = site.run("--apply", "--native-dir", str(site.root / "nowhere"), "--native-data", str(site.root / "nowhere/data"))
    assert result.returncode == 2 and "no native install" in result.stderr


def test_it_refuses_while_a_native_process_runs_outside_systemd(site):
    (site.state / "moreopenrepeater.active").unlink()
    (site.state / "moreopenrepeater.enabled").unlink()
    site._shim("pgrep", "exit 0")
    result = site.run("--apply")
    assert result.returncode == 2 and "outside systemd" in result.stderr


def test_a_target_inside_the_native_data_is_refused(site):
    result = site.run("--apply", "--target", str(site.data / "container"))
    assert result.returncode == 2
    assert "native data" in result.stderr


# ---------------------------------------------------------------------------------------------------------------------
# apply

def test_apply_copies_the_data_with_the_right_modes_and_never_touches_the_originals(site):
    native_before = digest_tree(site.opt)
    result = site.run("--apply", setup_args_file=True)
    assert result.returncode == 0, result.stdout + result.stderr
    t = site.target
    assert json.loads((t / "state.json").read_text())["config"]["callsign"] == "W1AW"
    assert (t / "users.json").read_bytes() == (site.data / "users.json").read_bytes()
    for name in ("state.json", "users.json", "hub-sites.json", "activity.db", "audit.db"):
        assert stat.S_IMODE((t / name).stat().st_mode) == 0o600, name
    assert (t / "audio/clip1.wav").read_bytes() == (site.data / "audio/clip1.wav").read_bytes()
    assert (t / "audio/index.json").read_text() == "[]"
    assert stat.S_IMODE((t / "audio/clip1.wav").stat().st_mode) == 0o644
    # not carried: voices, the native backups, the native process marker, recordings (no --recordings)
    for name in ("piper", "backups", "run-state.json", "recordings"):
        assert not (t / name).exists(), name
    # the leftovers of a live database are folded in, not copied raw
    assert not any(p.name.endswith(("-wal", "-shm")) for p in t.iterdir())
    # and the native side is byte for byte what it was (including the -wal and -shm files, and no new ones)
    assert digest_tree(site.opt) == native_before


def test_the_sqlite_copy_has_the_rows_that_were_only_in_the_wal_file(site):
    site.run("--apply", setup_args_file=True)
    for name, rows in (("activity.db", 5), ("audit.db", 2)):
        conn = sqlite3.connect(site.target / name)
        try:
            assert conn.execute("PRAGMA integrity_check").fetchone() == ("ok",)
            assert conn.execute("SELECT count(*) FROM event").fetchone() == (rows,)
            assert conn.execute("PRAGMA journal_mode").fetchone() == ("delete",)
        finally:
            conn.close()


def test_recordings_are_copied_only_when_asked(site):
    site.run("--apply", "--recordings", setup_args_file=True)
    assert (site.target / "recordings/1700000000000.wav").exists()
    assert not (site.target / "piper").exists()


def test_apply_stops_the_native_service_first_and_prints_the_undo(site):
    result = site.run("--apply", setup_args_file=True)
    log = site.call_log()
    assert "sudo systemctl disable --now moreopenrepeater" in log
    assert log.index("sudo systemctl disable --now moreopenrepeater") < log.index(next(c for c in log if c.startswith("setup")))
    assert "OFF THE AIR" in result.stdout
    assert "sudo systemctl enable --now moreopenrepeater" in result.stdout


def test_sudo_is_used_only_to_read_stop_and_copy(site):
    site.run("--apply", setup_args_file=True)
    for call in (c for c in site.call_log() if c.startswith("sudo ")):
        assert re.match(r"sudo (cat |systemctl disable --now |\S*python)", call), call


def test_the_checklist_does_not_claim_success_on_the_air_and_gives_the_exact_rollback(site):
    out = site.run("--apply", setup_args_file=True).stdout
    assert "docker compose down" in out and "(no -v" in out
    assert "sudo systemctl enable --now moreopenrepeater" in out
    assert "STILL TO DO BY YOU, ON THE AIR" in out and "nothing here has proved any of it" in out
    assert "key the repeater from a radio" in out and "identification" in out and "audio levels" in out
    assert "ignored by the native version" in out
    for word in ("works on the air", "is on the air now", "verified on the air"):
        assert word not in out.lower()
    assert "—" not in out  # no em dash anywhere in what it says


def test_no_setup_stops_after_the_copy_and_prints_the_command(site):
    result = site.run("--apply", "--no-setup", setup_args_file=True)
    assert result.returncode == 0
    assert not any(c.startswith("setup") for c in site.call_log())
    assert "--no-setup" in result.stdout and "./setup.sh --udev" in result.stdout


def test_it_hands_over_to_setup_with_the_udev_flag_when_the_rule_is_missing(site):
    site.run("--apply", setup_args_file=True)
    assert "setup --udev" in site.call_log()
    (site.etc / "udev/rules.d/61-moreopenrepeater-ptt.rules").write_text("")
    # a second site has the rule, so no flag: use a fresh target
    again = Site(site.root / "again")
    (again.etc / "udev/rules.d/61-moreopenrepeater-ptt.rules").write_text("")
    again.run("--apply", setup_args_file=True)
    assert any(c.startswith("setup") for c in again.call_log()) and not any(c.startswith("setup --udev") for c in again.call_log())


def test_the_hardware_the_configuration_uses_is_passed_on_or_printed(site):
    config = {"callsign": "W1AW", "ptt_output": "gpio", "cos_source": "serial", "cos_serial_device": str(site.dev / "ttyUSB0"),
              "link_radio_enabled": True}
    (site.data / "state.json").write_text(json.dumps({"config": config}))
    (site.dev / "ttyUSB0").write_text("")
    (site.etc / "udev/rules.d/61-moreopenrepeater-ptt.rules").write_text("")
    result = site.run("--apply", setup_args_file=True)
    setup_call = next(c for c in site.call_log() if c.startswith("setup"))
    assert "--gpio" in setup_call and f"--serial {site.dev}/ttyUSB0" in setup_call
    assert "--no-start" in setup_call  # a link radio needs its own interface named first
    assert "--link-radio ID" in result.stdout


# ---------------------------------------------------------------------------------------------------------------------
# the settings

def test_each_key_goes_to_the_file_it_belongs_in(site):
    site.run("--apply", "--no-setup")
    assert site.value("api.env", "MOREOPENREPEATER_AUTH_USER") == "admin"
    assert site.value("api.env", "MOREOPENREPEATER_AUTH_PASSWORD") == AUTH_PASSWORD
    assert site.value("api.env", "MOREOPENREPEATER_HOMEASSISTANT_TOKEN") == HA_TOKEN
    assert site.value("core.env", "MOREOPENREPEATER_STALL_SECONDS") == "15"
    assert site.value("core.env", "MOREOPENREPEATER_AUTH_PASSWORD") is None  # the login is the dashboard's, not the core's
    assert site.value("api.env", "MOREOPENREPEATER_STALL_SECONDS") is None
    assert site.value(".env", "MOR_API_PORT") == "8080"
    assert site.value(".env", "MOR_API_HOST") is None  # 0.0.0.0: setup.sh decides, after sign-in is on
    assert site.value(".env", "MOR_DATA_DIR") == str(site.target)
    # the native-only keys are not copied
    for name in ("api.env", "core.env", ".env"):
        text = (site.docker / name).read_text()
        for key in ("UPDATE_CHANNEL=", "PROTECT_SD=", "PIPER_VOICE", "CM108_HIDRAW=auto"):
            assert not re.search(rf"^[^#\n]*{re.escape(key)}", text, re.M), (name, key)


def test_a_loopback_ami_is_not_copied_because_there_it_would_mean_the_container_itself(site):
    result = site.run("--apply", "--no-setup")
    for name in ("api.env", "core.env"):
        for key in ("AMI_HOST", "AMI_PORT", "AMI_USER", "AMI_SECRET"):
            assert site.value(name, f"MOREOPENREPEATER_{key}") is None
    assert "points at the Asterisk on this machine" in result.stdout


def test_a_remote_ami_goes_to_both_services(site):
    env = NATIVE_ENV.replace("AMI_HOST=127.0.0.1", "AMI_HOST=192.168.1.9") + "MOREOPENREPEATER_AMI_NODE=1999\n"
    (site.etc / "moreopenrepeater/env").write_text(env)
    result = site.run("--apply", "--no-setup")
    for name in ("api.env", "core.env"):
        assert site.value(name, "MOREOPENREPEATER_AMI_HOST") == "192.168.1.9"
        assert site.value(name, "MOREOPENREPEATER_AMI_PORT") == "5038"
        assert site.value(name, "MOREOPENREPEATER_AMI_SECRET") == AMI_SECRET
    assert site.value("api.env", "MOREOPENREPEATER_AMI_NODE") == "1999"
    assert site.value("core.env", "MOREOPENREPEATER_AMI_NODE") is None  # the dashboard reads the node number
    no_secrets(result)


def test_the_loopback_dashboard_setting_is_kept(site):
    (site.etc / "moreopenrepeater/env").write_text(NATIVE_ENV.replace("HOST=0.0.0.0", "HOST=127.0.0.1"))
    site.run("--apply", "--no-setup")
    assert site.value(".env", "MOR_API_HOST") == "127.0.0.1"


def test_unknown_keys_are_listed_and_not_guessed_or_printed(site):
    result = site.run("--apply", "--no-setup")
    assert "UNKNOWN" in result.stdout and "MOREOPENREPEATER_SOMETHING_NEW" in result.stdout
    for name in ("api.env", "core.env", ".env"):
        assert "SOMETHING_NEW" not in (site.docker / name).read_text()
    no_secrets(result)


def test_the_settings_files_are_readable_by_the_owner_only(site):
    site.run("--apply", "--no-setup")
    for name in ("api.env", "core.env", ".env"):
        assert stat.S_IMODE((site.docker / name).stat().st_mode) == 0o600, name


def test_a_value_already_set_in_the_target_is_kept(site):
    (site.docker / "api.env").write_text("MOREOPENREPEATER_AUTH_USER=newadmin\nMOREOPENREPEATER_AUTH_PASSWORD=already-here\n")
    (site.docker / "core.env").write_text("MOREOPENREPEATER_STALL_SECONDS=30\n")
    result = site.run("--apply", "--no-setup")
    assert site.value("api.env", "MOREOPENREPEATER_AUTH_USER") == "newadmin"
    assert site.value("api.env", "MOREOPENREPEATER_AUTH_PASSWORD") == "already-here"
    assert site.value("core.env", "MOREOPENREPEATER_STALL_SECONDS") == "30"
    assert "kept" in result.stdout
    no_secrets(result)


def test_a_value_with_special_characters_is_written_so_compose_reads_it_literally(site):
    odd = "pa$$ w0rd #1"
    (site.etc / "moreopenrepeater/env").write_text(f'MOREOPENREPEATER_AUTH_USER=admin\nMOREOPENREPEATER_AUTH_PASSWORD="{odd}"\n')
    result = site.run("--apply", "--no-setup")
    assert site.value("api.env", "MOREOPENREPEATER_AUTH_PASSWORD") == f"'{odd}'"  # single quotes: Compose does not expand $ in them
    assert odd not in result.stdout + result.stderr


def test_half_a_login_is_not_copied_and_says_so(site):
    (site.etc / "moreopenrepeater/env").write_text("MOREOPENREPEATER_AUTH_USER=admin\n")
    result = site.run("--apply", "--no-setup")
    assert site.value("api.env", "MOREOPENREPEATER_AUTH_USER") is None
    assert "only one of AUTH_USER and AUTH_PASSWORD" in result.stdout


# ---------------------------------------------------------------------------------------------------------------------
# secrets, whatever happens

def test_no_secret_is_ever_printed_or_passed_on_a_command_line(site):
    env = NATIVE_ENV.replace("AMI_HOST=127.0.0.1", "AMI_HOST=10.0.0.5")
    (site.etc / "moreopenrepeater/env").write_text(env)
    results = [site.run(), site.run("--apply", setup_args_file=True), site.run("--apply", "--into-existing")]
    no_secrets(*results)
    for call in site.call_log():
        for secret in SECRETS:
            assert secret not in call


# ---------------------------------------------------------------------------------------------------------------------
# into an existing folder, and running again

def test_into_existing_never_overwrites_a_file_without_force(site):
    site.target.mkdir()
    (site.target / "state.json").write_text('{"config": {"callsign": "KEEP"}}')
    result = site.run("--apply", "--into-existing", "--no-setup")
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads((site.target / "state.json").read_text())["config"]["callsign"] == "KEEP"
    assert "kept         state.json" in result.stdout
    assert (site.target / "users.json").exists()  # what was missing was added


def test_force_replaces_it_and_keeps_the_old_one_aside(site):
    site.target.mkdir()
    (site.target / "state.json").write_text('{"config": {"callsign": "OLD"}}')
    result = site.run("--apply", "--into-existing", "--force", "--no-setup")
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads((site.target / "state.json").read_text())["config"]["callsign"] == "W1AW"
    assert json.loads((site.target / "state.json.before-migrate").read_text())["config"]["callsign"] == "OLD"


def test_force_without_into_existing_is_refused(site):
    assert site.run("--apply", "--force").returncode == 2


def test_running_it_again_changes_nothing(site):
    first = site.run("--apply", "--no-setup")
    assert first.returncode == 0, first.stdout + first.stderr
    settled = digest_tree(site.root)
    second = site.run("--apply", "--into-existing", "--no-setup")
    assert second.returncode == 0, second.stdout + second.stderr
    assert digest_tree(site.root) == settled
    assert "kept         state.json" in second.stdout
    assert "was not running, so it was not stopped" in second.stdout or "not running, so it was not stopped" in second.stdout
    # and a plain second run, without --into-existing, refuses rather than touching anything
    third = site.run("--apply", "--no-setup")
    assert third.returncode == 2 and digest_tree(site.root) == settled


# ---------------------------------------------------------------------------------------------------------------------
# a copy that goes wrong, and files that are not what they look like

def test_a_broken_database_stops_the_copy_and_leaves_nothing_behind(site):
    (site.data / "audit.db").write_bytes(b"this is not a database" * 50)
    (site.data / "audit.db-wal").unlink()
    (site.data / "audit.db-shm").unlink()
    native_before = digest_tree(site.opt)
    result = site.run("--apply", "--no-setup")
    assert result.returncode == 1
    assert "the copy failed" in result.stdout and "removed again" in result.stdout
    assert not any(site.target.glob("*")), list(site.target.glob("*"))
    assert digest_tree(site.opt) == native_before
    assert "sudo systemctl enable --now moreopenrepeater" in result.stdout  # the way back is printed


def test_a_symbolic_link_in_the_data_folder_is_not_followed(site, tmp_path):
    secret = tmp_path / "outside-secret.txt"
    secret.write_text("not for the container")
    (site.data / "audio/sneaky.wav").symlink_to(secret)
    result = site.run("--apply", "--no-setup")
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (site.target / "audio/sneaky.wav").exists()
    assert "symbolic link, not followed" in result.stdout


# ---------------------------------------------------------------------------------------------------------------------
# the hand-off to the real setup.sh

def test_the_real_setup_keeps_the_owners_login_and_starts_the_stack(site):
    (site.etc / "udev/rules.d/61-moreopenrepeater-ptt.rules").write_text("")
    result = site.run("--apply")
    assert result.returncode == 0, result.stdout + result.stderr
    assert site.value("api.env", "MOREOPENREPEATER_AUTH_USER") == "admin"
    assert site.value("api.env", "MOREOPENREPEATER_AUTH_PASSWORD") == AUTH_PASSWORD  # not replaced by a generated one
    assert "Sign in as" not in result.stdout  # setup.sh only shows a password it generated
    assert json.loads((site.target / "state.json").read_text())["config"]["tot_duration"] == 120  # not re-seeded
    assert site.value(".env", "MOR_DATA_DIR") == str(site.target)
    assert "docker compose up -d --build" in site.call_log()
    assert site.value(".env", "MOR_API_HOST") == "0.0.0.0"  # on, because the login is on
    no_secrets(result)


def test_a_native_install_with_no_login_gets_one_generated_by_setup(site):
    (site.etc / "moreopenrepeater/env").write_text("MOREOPENREPEATER_PORT=8000\n")
    (site.etc / "udev/rules.d/61-moreopenrepeater-ptt.rules").write_text("")
    result = site.run("--apply")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Sign in as" in result.stdout
    assert "no login set" in result.stdout


def test_when_setup_fails_the_way_back_is_printed(site):
    # the PTT name rule is missing and setup.sh is given a fake that fails
    fake = site.root / "failing-setup.sh"
    fake.write_text("echo 'setup: something went wrong' >&2\nexit 2\n")
    result = site.run("--apply", MOR_MIGRATE_SETUP=str(fake))
    assert result.returncode == 1
    assert "setup.sh stopped with status 2" in result.stdout
    assert "docker compose down" in result.stdout and "sudo systemctl enable --now moreopenrepeater" in result.stdout
    assert (site.target / "state.json").exists()  # the data stays in place


# ---------------------------------------------------------------------------------------------------------------------
# the script itself

def test_the_script_has_no_em_dash_and_help_works(site):
    text = (DOCKER / "migrate-from-native.sh").read_text()
    assert "—" not in text
    result = site.run("--help")
    assert result.returncode == 0 and "--apply" in result.stdout and "--into-existing" in result.stdout


def test_an_unknown_option_is_refused(site):
    result = site.run("--bogus")
    assert result.returncode == 2 and "unknown option" in result.stderr


def test_shellcheck_is_clean_when_it_is_installed():
    shellcheck = shutil.which("shellcheck")
    if shellcheck is None:
        pytest.skip("shellcheck is not installed")
    result = subprocess.run([shellcheck, str(DOCKER / "migrate-from-native.sh")], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout
