#!/bin/sh
# Move an existing native install (scripts/install-pi.sh) into the container install (docs/docker.md, Moving from the
# native install; issue #233). Run it as yourself on the same machine, from a checkout of this repository:
#
#   cd docker
#   ./migrate-from-native.sh            # a DRY RUN: says what it found and what it would do, changes nothing
#   ./migrate-from-native.sh --apply    # does it
#
# Reading the native files needs sudo (they belong to the moreopenrepeater user), and so does stopping the service and
# writing into a data folder the container owns. The script says so, and uses sudo only for those steps.
#
# What --apply does, in this order, and stops at the first thing that is wrong:
#   1. Refuses (changing nothing) if Docker is missing or can't be asked, if the container stack is already running,
#      if the native data has no usable state.json, or if the target data folder is not empty (unless --into-existing).
#   2. Reads the native settings and the native data (read only), and works out the whole plan before touching anything.
#   3. Stops the native service (the repeater goes off the air), and prints the exact command that undoes it.
#   4. Copies the data into MOR_DATA_DIR as uid 999: the saved configuration, accounts, audio clips, the history
#      databases (a consistent SQLite copy, never a raw copy of a live file), the hub registry and the rest of the
#      data folder's own files. Recordings only with --recordings. The originals are only ever read.
#   5. Translates /etc/moreopenrepeater/env into api.env, core.env and .env, keeping any value already set there.
#      Secrets travel file to file and are never printed.
#   6. Hands over to ./setup.sh, which writes the rest, keeps the login that was copied, and starts the containers.
#   7. Prints what was copied, what was not and why, the exact rollback, and what you must check on the air.
#
# Going back is real because the native data is only read: stop the stack, start the native service (printed at the end).
#
#   --apply            do it (the default is a dry run)
#   --into-existing    the target data folder may already hold files. It never overwrites one without --force.
#   --force            with --into-existing: replace a file that is already there (the old one is kept as NAME.before-migrate)
#   --recordings       also copy the saved transmissions (they add up; the audio clips are always copied)
#   --native-dir DIR   the native install (default /opt/moreopenrepeater)
#   --native-data DIR  the native data folder, if it is not DATA_DIR in the env file or DIR/data
#   --target DIR       the container install's data folder (default: MOR_DATA_DIR in .env, else docker/data)
#   --no-setup         copy and translate, then stop: run ./setup.sh yourself (the command is printed)
#
# Hooks for the tests (never needed on a real machine): MOR_MIGRATE_ETC (the /etc to look in), MOR_MIGRATE_OPT (the /opt
# for DVSwitch), MOR_MIGRATE_PYTHON, MOR_MIGRATE_SETUP, MOR_MIGRATE_CHOWN=0 (skip giving files to uid 999), MOR_MIGRATE_PGREP,
# MOR_DEV_DIR and MOR_TAILSCALE_SOCKET_DIR (as in setup.sh).
set -eu

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

APPLY=0
FORCE=0
INTO_EXISTING=0
RECORDINGS=0
NO_SETUP=0
OPT="${MOR_MIGRATE_OPT:-/opt}"
NATIVE_DIR="$OPT/moreopenrepeater"
NATIVE_DATA=""
TARGET=""
while [ $# -gt 0 ]; do
  case "$1" in
    --apply) APPLY=1 ;;
    --force) FORCE=1 ;;
    --into-existing) INTO_EXISTING=1 ;;
    --recordings) RECORDINGS=1 ;;
    --no-setup) NO_SETUP=1 ;;
    --native-dir) [ -n "${2:-}" ] || { echo "migrate: --native-dir needs the folder" >&2; exit 2; }; NATIVE_DIR="$2"; shift ;;
    --native-data) [ -n "${2:-}" ] || { echo "migrate: --native-data needs the folder" >&2; exit 2; }; NATIVE_DATA="$2"; shift ;;
    --target) [ -n "${2:-}" ] || { echo "migrate: --target needs the folder" >&2; exit 2; }; TARGET="$2"; shift ;;
    -h|--help) sed -n '2,36p' "$HERE/migrate-from-native.sh" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "migrate: unknown option $1 (see --help)" >&2; exit 2 ;;
  esac
  shift
done
[ "$FORCE" = 0 ] || [ "$INTO_EXISTING" = 1 ] || { echo "migrate: --force only makes sense with --into-existing" >&2; exit 2; }

say() { printf '%s\n' "$*"; }
die() { printf 'migrate: %s\n' "$*" >&2; exit 2; }
as_root() { if [ "$(id -u)" = 0 ]; then "$@"; else sudo "$@"; fi; }

ETC="${MOR_MIGRATE_ETC:-/etc}"
DEV="${MOR_DEV_DIR:-/dev}"
SETUP="${MOR_MIGRATE_SETUP:-$HERE/setup.sh}"
CHOWN="${MOR_MIGRATE_CHOWN:-1}"
PGREP="${MOR_MIGRATE_PGREP:-pgrep}"
TS_DIR="${MOR_TAILSCALE_SOCKET_DIR:-/var/run/tailscale}"
ENV_FILE="$ETC/moreopenrepeater/env"
SYSTEMD="$ETC/systemd/system"
RULES="$ETC/udev/rules.d"
ASTERISK="$ETC/asterisk"

# value FILE NAME: the value of an uncommented NAME= line, or nothing (the same reading setup.sh does)
value() { [ -f "$1" ] && sed -n "s/^$2=//p" "$1" | tail -n 1 || true; }

BLOCK=""
block() { BLOCK="$BLOCK
  - $*"; }

# --- python, for the parts a shell does badly: env files, JSON and SQLite -------------------------------------------

PY="${MOR_MIGRATE_PYTHON:-}"
[ -n "$PY" ] || PY="$(command -v python3 2>/dev/null || true)"
[ -n "$PY" ] || { [ -x "$NATIVE_DIR/.venv/bin/python" ] && PY="$NATIVE_DIR/.venv/bin/python"; } || true
[ -n "$PY" ] || die "python3 is not installed (the native install needs it too). Install it: sudo apt install python3"

HELPER='
import hashlib, json, os, re, shutil, sqlite3, stat, sys, tempfile

PREFIX = "MOREOPENREPEATER_"

def out(text=""):
    print(text)

def fact(name, value):
    print("@%s=%s" % (name, value))

# ---------------------------------------------------------------------------------------------------------------------
# what the data folder holds

JSON_STATE = ["state.json", "users.json", "tokens.json", "agent-access.json", "hub-sites.json", "alerts.json",
              "nets.json", "control-codes.json", "stream.json", "log-shipping.json"]
DATABASES = ["activity.db", "audit.db", "aprs.db", "sensors.db"]
FOLDERS = ["audio", "mailbox"]
RECORDING_FOLDERS = ["recordings", "monitor-recordings"]
WHY_NOT = {
    "piper": "voice models; the container install has its own Piper container with its own voices",
    "kokoro": "voice models; Kokoro is not part of the container install",
    "vosk-model": "speech model (large); the container install downloads its own from the dashboard",
    "backups": "the native install'"'"'s own backups; kept where they are as your safety copy",
    "run-state.json": "a marker for the native process, meaningless to the container",
    "auto-update.json": "the native updater'"'"'s schedule; the container install updates with docker/update.sh",
    "update-request": "a request file for the native updater",
    "restart-request": "a request file for the native service",
    "tailscale-request": "a request file for the native service",
    "dmr-restart-request": "a request file for the native DMR bridge",
    "allstar-nodes.txt": "a downloaded node list; the container fetches its own",
}

def is_state(name):
    return name in JSON_STATE or name in DATABASES

def why_not(name):
    if name in WHY_NOT:
        return WHY_NOT[name]
    if name.endswith(".corrupt") or name.endswith(".tmp") or name.endswith(".migrating"):
        return "a leftover the app set aside; look at it by hand if you need it"
    if name.endswith("-wal") or name.endswith("-shm"):
        return "the database journal; it is folded into the database copy"
    return "not a file the container install knows"

def lstat(path):
    try:
        return os.lstat(path)
    except OSError:
        return None

def count_files(path):
    n = 0
    for _root, _dirs, files in os.walk(path):
        n += len(files)
    return n

def inventory(data, recordings):
    """(things to copy, things left alone, symlinks skipped); each thing is (name, kind, mode)."""
    copy, left, links = [], [], []
    names = sorted(os.listdir(data))
    for name in names:
        st = lstat(os.path.join(data, name))
        if st is None:
            continue
        if stat.S_ISLNK(st.st_mode):
            links.append(name)
            continue
        wanted_recordings = name in RECORDING_FOLDERS
        if name in JSON_STATE and stat.S_ISREG(st.st_mode):
            copy.append((name, "file", 0o600))
        elif name in DATABASES and stat.S_ISREG(st.st_mode):
            copy.append((name, "db", 0o600))
        elif name in FOLDERS and stat.S_ISDIR(st.st_mode):
            copy.append((name, "dir", 0o644))
        elif wanted_recordings and stat.S_ISDIR(st.st_mode):
            if recordings:
                copy.append((name, "dir", 0o644))
            else:
                total = count_files(os.path.join(data, name))
                left.append((name, "%d file%s; add --recordings to copy them" % (total, "" if total == 1 else "s")))
        elif name.endswith("-wal") or name.endswith("-shm"):
            continue  # folded into the database copy
        else:
            left.append((name, why_not(name)))
    return copy, left, links

def scan(data, recordings):
    if not os.path.isdir(data):
        out("  the native data folder %s does not exist" % data)
        fact("state_ok", 0)
        fact("state_why", "there is no data folder at %s" % data)
        return
    state_path = os.path.join(data, "state.json")
    st = lstat(state_path)
    problem = None
    callsign = ""
    config = {}
    if st is None:
        problem = "there is no state.json in %s" % data
    elif not stat.S_ISREG(st.st_mode):
        problem = "%s is not a regular file" % state_path
    else:
        try:
            with open(state_path) as handle:
                state = json.load(handle)
            config = state.get("config") if isinstance(state, dict) else None
            if not isinstance(config, dict):
                problem = "state.json has no configuration section"
            else:
                callsign = str(config.get("callsign") or "").strip()
                if not callsign:
                    problem = "state.json has no callsign (the core will not start without one)"
        except (OSError, ValueError) as error:
            problem = "state.json can not be read as JSON (%s)" % error.__class__.__name__
    fact("state_ok", 0 if problem else 1)
    if problem:
        fact("state_why", problem)
    if callsign:
        fact("callsign", callsign)
    copy, left, links = inventory(data, recordings)
    descriptions = {
        "state.json": "the saved configuration, macros and announcements" + ((", callsign " + callsign) if callsign else ""),
        "users.json": "dashboard accounts (password hashes)",
        "tokens.json": "API tokens",
        "agent-access.json": "agent access settings",
        "hub-sites.json": "the multi-site hub registry (holds tokens)",
        "alerts.json": "alert settings",
        "nets.json": "nets",
        "control-codes.json": "control codes",
        "stream.json": "streaming settings",
        "log-shipping.json": "log shipping settings",
        "activity.db": "reception, airtime and ID history (SQLite, copied with the backup API)",
        "audit.db": "the audit log (SQLite, copied with the backup API)",
        "aprs.db": "APRS stations (SQLite, copied with the backup API)",
        "sensors.db": "sensor history (SQLite, copied with the backup API)",
        "audio": "audio clips and their index.json",
        "mailbox": "the mailbox",
        "recordings": "saved transmissions",
        "monitor-recordings": "saved monitor receiver recordings",
    }
    for name, kind, _mode in copy:
        note = descriptions.get(name, "")
        if kind == "dir":
            note = "%d files, %s" % (count_files(os.path.join(data, name)), note)
        out("  will copy    %-20s %s" % (name, note))
    for name, why in left:
        out("  left behind  %-20s %s" % (name, why))
    for name in links:
        out("  left behind  %-20s a symbolic link; not followed (copy what it points at by hand if you need it)" % name)
    # what the saved configuration needs from the host, to suggest the right setup.sh flags
    gpio = config.get("ptt_output") == "gpio" or config.get("cos_source") == "gpio" or config.get("fan_output") == "gpio" \
        or bool(config.get("gpio_pins")) or (config.get("monitor_enabled") and config.get("monitor_squelch") == "gpio")
    link = bool(config.get("link_radio_enabled"))
    if link and (config.get("link_radio_ptt") == "gpio" or config.get("link_radio_cos") == "gpio"):
        gpio = True
    serials = []
    for key in ("ptt_serial_device", "cos_serial_device", "link_radio_ptt_serial_device", "link_radio_cos_serial_device"):
        device = str(config.get(key) or "").strip()
        uses_it = (key.startswith("ptt") and config.get("ptt_output") == "serial") or (key.startswith("cos") and config.get("cos_source") == "serial") \
            or (key.startswith("link_radio_ptt") and link and config.get("link_radio_ptt") == "serial") \
            or (key.startswith("link_radio_cos") and link and config.get("link_radio_cos") == "serial")
        if device and uses_it and device not in serials:
            serials.append(device)
    fact("ptt_output", config.get("ptt_output") or "cm108")
    fact("hint_gpio", 1 if gpio else 0)
    fact("hint_link_radio", 1 if link else 0)
    fact("hint_serials", " ".join(serials))

# ---------------------------------------------------------------------------------------------------------------------
# the copy

class Undo(Exception):
    pass

def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()

def open_source(path):
    """Open a regular file without following a symbolic link (the native data folder is writable by the service user)."""
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise OSError("not a regular file: %s" % path)
    return fd

class Copier:
    def __init__(self, target, chown, force, into_existing):
        self.target = target
        self.chown = chown
        self.force = force
        self.into_existing = into_existing
        self.created_files = []
        self.created_dirs = []
        self.moved_aside = []
        self.copied = []
        self.kept = []

    def own(self, path):
        if self.chown:
            os.chown(path, 999, 999)

    def make_dir(self, path):
        if os.path.islink(path):
            raise Undo("%s is a symbolic link" % path)
        if not os.path.isdir(path):
            os.makedirs(path, 0o755)
            self.created_dirs.append(path)
            self.own(path)

    def prepare(self, dest):
        """True if dest may be written. An existing file is kept unless --force, then moved aside."""
        if os.path.islink(dest):
            raise Undo("%s is a symbolic link; not writing through it" % dest)
        if os.path.lexists(dest):
            if not self.force:
                self.kept.append(dest)
                return False
            aside = dest + ".before-migrate"
            if os.path.lexists(aside):
                raise Undo("%s already exists; move it away first" % aside)
            os.rename(dest, aside)
            self.moved_aside.append((dest, aside))
        return True

    def finish(self, tmp, dest, mode):
        os.chmod(tmp, mode)
        self.own(tmp)
        os.replace(tmp, dest)
        self.created_files.append(dest)

    def copy_file(self, src, dest, mode):
        if not self.prepare(dest):
            return False
        tmp = dest + ".migrating"
        fd_in = open_source(src)
        try:
            fd_out = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            self.created_files.append(tmp)
            digest = hashlib.sha256()
            with os.fdopen(fd_in, "rb") as source, os.fdopen(fd_out, "wb") as sink:
                fd_in = None
                for block in iter(lambda: source.read(1 << 20), b""):
                    digest.update(block)
                    sink.write(block)
                sink.flush()
                os.fsync(sink.fileno())
        finally:
            if fd_in is not None:
                os.close(fd_in)
        self.finish(tmp, dest, mode)
        self.created_files.remove(tmp)
        if sha256_of(dest) != digest.hexdigest():
            raise Undo("%s did not copy exactly" % dest)
        return True

    def copy_database(self, src, dest, mode):
        if not self.prepare(dest):
            return False
        stage = tempfile.mkdtemp(prefix="mor-migrate-")
        try:
            staged = os.path.join(stage, "db")
            for suffix in ("", "-wal"):
                path = src + suffix
                if suffix and lstat(path) is None:
                    continue
                fd = open_source(path)
                with os.fdopen(fd, "rb") as source, open(staged + suffix, "wb") as sink:
                    shutil.copyfileobj(source, sink)
            tmp = dest + ".migrating"
            self.created_files.append(tmp)
            try:
                source_db = sqlite3.connect(staged)
                dest_db = sqlite3.connect(tmp)
                try:
                    source_db.backup(dest_db)
                    dest_db.execute("PRAGMA journal_mode=DELETE")
                    dest_db.commit()
                    check = dest_db.execute("PRAGMA integrity_check").fetchone()
                finally:
                    dest_db.close()
                    source_db.close()
            except sqlite3.DatabaseError as error:
                raise Undo("%s is not a database SQLite can read (%s)" % (os.path.basename(src), error))
            if check != ("ok",):
                raise Undo("the copy of %s failed SQLite'"'"'s integrity check" % os.path.basename(src))
            self.finish(tmp, dest, mode)
            self.created_files.remove(tmp)
        finally:
            shutil.rmtree(stage, ignore_errors=True)
        return True

    def copy_dir(self, src, dest, mode):
        self.make_dir(dest)
        done = 0
        for entry in sorted(os.listdir(src)):
            path = os.path.join(src, entry)
            st = os.lstat(path)
            if stat.S_ISLNK(st.st_mode):
                out("  skipped      %s: a symbolic link, not followed" % os.path.relpath(path, os.path.dirname(src)))
            elif stat.S_ISDIR(st.st_mode):
                done += self.copy_dir(path, os.path.join(dest, entry), mode)
            elif stat.S_ISREG(st.st_mode):
                done += 1 if self.copy_file(path, os.path.join(dest, entry), mode) else 0
        return done

    def undo(self):
        for path in reversed(self.created_files):
            try:
                os.unlink(path)
            except OSError:
                pass
        for dest, aside in reversed(self.moved_aside):
            try:
                os.replace(aside, dest)
            except OSError:
                pass
        for path in reversed(self.created_dirs):
            try:
                os.rmdir(path)
            except OSError:
                pass

def copy(data, target, recordings, chown, force, into_existing):
    items, _left, _links = inventory(data, recordings)
    worker = Copier(target, chown, force, into_existing)
    try:
        worker.make_dir(target)
        worker.own(target)
        for name, kind, mode in items:
            src = os.path.join(data, name)
            dest = os.path.join(target, name)
            if kind == "file":
                done = worker.copy_file(src, dest, mode)
                out("  copied       %s (mode %s)" % (name, oct(mode)[2:]) if done else "  kept         %s: already in the target, not overwritten (--force replaces it)" % name)
            elif kind == "db":
                done = worker.copy_database(src, dest, mode)
                out("  copied       %s (SQLite backup, integrity checked, mode %s)" % (name, oct(mode)[2:]) if done else "  kept         %s: already in the target, not overwritten (--force replaces it)" % name)
            else:
                count = worker.copy_dir(src, dest, mode)
                out("  copied       %s/ (%d files)" % (name, count))
    except (Undo, OSError, sqlite3.Error) as error:
        worker.undo()
        out("migrate: the copy failed (%s). What this run had written was removed again; the native data was only read." % error)
        sys.exit(1)
    fact("copied", len(worker.created_files))

# ---------------------------------------------------------------------------------------------------------------------
# the env file

LOOPBACK = re.compile(r"^(localhost|::1|127\.[0-9.]+)$")
IPV4 = re.compile(r"^[0-9]{1,3}(\.[0-9]{1,3}){3}$")

AMI_REASON = "points at the Asterisk on this machine; in the container install the node is its own container (setup.sh --import-allstar), which writes its own management login"
SKIP = {
    "USRP_LISTEN": "compose.yaml sets it for the container",
    "USRP_ADDRESS": "compose.yaml sets it for the container",
    "AUDIOSOCKET_LISTEN": "compose.yaml sets it for the container",
    "AUDIOSOCKET_ADDRESS": "compose.yaml sets it for the container",
    "DMR_USRP_LISTEN": "compose.yaml sets it for the container",
    "DMR_USRP_PEER": "a DVSwitch bridge on this machine is not reachable from the containers; see the DMR note at the end",
    "DMR_DIR": "the DMR container has its own folder (DMR is not imported, see the note at the end)",
    "DMR_RESTART_REQUEST": "the DMR container has its own (compose.yaml)",
    "DMR_STATUS": "the DMR container has its own (compose.yaml)",
    "UPDATE_CHANNEL": "in the container install the channel is the git branch of this checkout (docs/docker.md, Updating)",
    "UPDATER": "the native updater; the container install updates with docker/update.sh",
    "UPDATE_REQUEST": "the native updater; the container install updates with docker/update.sh",
    "RESTART_REQUEST": "the native restart helper",
    "TAILSCALE": "the native Tailscale helper; the container only reads the host'"'"'s Tailscale socket",
    "TAILSCALE_REQUEST": "the native Tailscale helper",
    "PROTECT_SD": "see the SD card note at the end (the container counterpart is compose.protect-sd.yaml)",
    "LOG_PATH": "the container install logs through logd and Docker, not a log file",
    "LOG_FORMAT": "compose.yaml sets it",
    "DATA_DIR": "used to find the native data; the container'"'"'s data folder is MOR_DATA_DIR in .env",
    "BACKUP_DIR": "a host folder the containers can not see; set where backups go on the dashboard if you need it",
    "CM108_HIDRAW": "compose.yaml gives the core the PTT interface by its stable name (setup.sh --udev), not by hidraw number",
    "LINK_CM108_HIDRAW": "the link radio'"'"'s interface is given with setup.sh --link-radio",
    "VOSK_MODEL": "a host folder; the speech model is not copied (it is large). Download one again from the dashboard",
    "CORE_SOCKET": "compose.yaml sets it for the containers",
    "FORWARDED_ALLOW_IPS": "a proxy on another machine reaches the dashboard through Docker'"'"'s network and appears with a different address; set it in api.env by hand once you know the address",
}
OUTSIDE_PREFIX = {"PIPER_": "the container install has its own Piper container", "KOKORO_": "Kokoro is not part of the container install"}
SECRET = {"AUTH_PASSWORD", "AMI_SECRET", "HOMEASSISTANT_TOKEN", "TRANSCRIPTION_API_KEY"}
SHOWN = {"MOR_API_HOST", "MOR_API_PORT", "HOST", "PORT", "AMI_HOST", "AMI_PORT", "AMI_NODE", "STALL_SECONDS", "USRP_NODE", "UPDATE_CHANNEL", "PROTECT_SD", "DIAG_LATENCY"}

def parse_env(text):
    values = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", line)
        if not match:
            continue
        key, val = match.group(1), match.group(2)
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'"'"'":
            quote = val[0]
            val = val[1:-1]
            if quote == "\"":
                val = val.replace("\\\"", "\"").replace("\\\\", "\\")
        values[key] = val
    return values

def env_form(val):
    """A value as it must be written in an env file Compose reads, or None if it can not be written safely."""
    if re.fullmatch(r"[A-Za-z0-9_./+@:,=%^-]*", val):
        return val
    if "'"'"'" not in val and "\n" not in val and "\r" not in val:
        return "'"'"'" + val + "'"'"'"
    return None

def shown(name, val):
    return val if name in SHOWN else "set"

def classify(values):
    """Returns (carry, skip, unknown): carry is [(file, key, value)], skip is [(name, why)], unknown is [name]."""
    carry, skip, unknown = [], [], []
    mine = {k[len(PREFIX):]: v for k, v in values.items() if k.startswith(PREFIX)}
    others = [k for k in values if not k.startswith(PREFIX)]
    for name in sorted(mine):
        val = mine[name]
        if name in ("AUTH_USER", "AUTH_PASSWORD"):
            continue  # decided together below
        if name.startswith("AMI_"):
            host = mine.get("AMI_HOST", "")
            if not host or LOOPBACK.match(host):
                skip.append((name, AMI_REASON if host else "the AMI link is off (no AMI_HOST), as it is here"))
            elif name == "AMI_NODE":
                carry.append(("api.env", PREFIX + name, val))
            else:
                carry.append(("api.env", PREFIX + name, val))
                carry.append(("core.env", PREFIX + name, val))
        elif name == "USRP_NODE":
            node_host = val.rsplit(":", 1)[0]
            if LOOPBACK.match(node_host):
                skip.append((name, "points at the Asterisk on this machine; the AllStar page sets the container node up (setup.sh --import-allstar)"))
            else:
                carry.append(("core.env", PREFIX + name, val))
        elif name in ("STALL_SECONDS", "DIAG_LATENCY"):
            carry.append(("core.env", PREFIX + name, val))
        elif name in ("HOMEASSISTANT_TOKEN", "TRANSCRIPTION_API_KEY"):
            carry.append(("api.env", PREFIX + name, val))
        elif name == "HOST":
            if LOOPBACK.match(val):
                carry.append((".env", "MOR_API_HOST", "127.0.0.1"))
            elif IPV4.match(val) and val != "0.0.0.0":
                carry.append((".env", "MOR_API_HOST", val))
            else:
                skip.append((name, "setup.sh publishes the dashboard on every interface once sign-in is on (use --localhost on it to keep the loopback)"))
        elif name == "PORT":
            if val.isdigit() and 0 < int(val) < 65536:
                carry.append((".env", "MOR_API_PORT", val))
            else:
                skip.append((name, "not a port number"))
        elif name in SKIP:
            skip.append((name, SKIP[name]))
        else:
            hit = [why for prefix, why in OUTSIDE_PREFIX.items() if name.startswith(prefix)]
            if hit:
                skip.append((name, hit[0]))
            else:
                unknown.append(PREFIX + name)
    for key in others:
        if key.startswith("PIPER_") or key.startswith("KOKORO_"):
            skip.append((key, [why for p, why in OUTSIDE_PREFIX.items() if key.startswith(p)][0]))
        elif key == "FORWARDED_ALLOW_IPS":
            skip.append((key[0:], SKIP["FORWARDED_ALLOW_IPS"]))
        else:
            unknown.append(key)
    user, password = mine.get("AUTH_USER", ""), mine.get("AUTH_PASSWORD", "")
    login = "none"
    if user and password:
        carry.append(("api.env", PREFIX + "AUTH_USER", user))
        carry.append(("api.env", PREFIX + "AUTH_PASSWORD", password))
        login = "carried"
    elif user or password:
        login = "half"
    return carry, skip, unknown, login

def envparse():
    values = parse_env(sys.stdin.read())
    carry, skip, unknown, login = classify(values)
    json.dump({"carry": carry, "skip": skip, "unknown": unknown, "login": login, "keys": sorted(values)}, sys.stdout)

def existing_value(path, key):
    try:
        with open(path) as handle:
            found = ""
            for line in handle.read().splitlines():
                if line.startswith(key + "="):
                    found = line[len(key) + 1:]
            return found
    except OSError:
        return ""

def ensure_file(path, example):
    if os.path.exists(path):
        os.chmod(path, 0o600)
        return
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as handle:
        if os.path.exists(example):
            with open(example) as source:
                handle.write(source.read())

def append_line(path, line):
    with open(path, "rb") as handle:
        data = handle.read()
    with open(path, "ab") as handle:
        if data and not data.endswith(b"\n"):
            handle.write(b"\n")
        handle.write((line + "\n").encode())

def envwrite(folder, apply):
    plan = json.load(sys.stdin)
    login = plan["login"]
    out("  the owner'"'"'s settings (names only; values are shown only where they are not secret):")
    by_file = {}
    for file, key, val in plan["carry"]:
        by_file.setdefault(file, []).append((key, val))
    wrote = 0
    for file in ("api.env", "core.env", ".env"):
        entries = by_file.get(file, [])
        path = os.path.join(folder, file)
        if entries and apply:
            ensure_file(path, path + ".example")
        for key, val in entries:
            name = key[len(PREFIX):] if key.startswith(PREFIX) else key
            label = "(secret)" if name in SECRET else "= " + shown(name, val)
            if existing_value(path, key):
                out("    kept         %-42s in %-8s already set there %s" % (key, file, "" if name in SECRET else "(not changed)"))
                continue
            form = env_form(val)
            if form is None:
                out("    BY HAND      %-42s in %-8s the value has a quote or a line break in it; copy it yourself from the native env file" % (key, file))
                continue
            if apply:
                append_line(path, "%s=%s" % (key, form))
                wrote += 1
                out("    wrote        %-42s to %-8s %s" % (key, file, label))
            else:
                out("    would write  %-42s to %-8s %s" % (key, file, label))
    if login == "carried":
        out("    The dashboard login (user and password) comes over as it is, so the owner'"'"'s admin login keeps working; setup.sh keeps a login that is already in api.env.")
    elif login == "half":
        out("    BY HAND      the native env file sets only one of AUTH_USER and AUTH_PASSWORD, so neither is copied; setup.sh will generate a new admin login and print it once.")
    else:
        out("    The native install had no login set, so setup.sh will generate a new admin login and print it once.")
    grouped = {}
    for name, why in sorted(plan["skip"]):
        label = PREFIX + name if not name.startswith(("PIPER_", "KOKORO_", "FORWARDED_")) else name
        grouped.setdefault(why, []).append(label)
    for why, labels in grouped.items():
        names = labels[0] + "".join(", " + l[len(PREFIX):] if l.startswith(PREFIX) else ", " + l for l in labels[1:])
        out("    not carried  %s" % names)
        out("                   %s" % why)
    for name in sorted(plan["unknown"]):
        out("    UNKNOWN      %-42s not copied (the script does not guess where it belongs; add it to api.env or core.env by hand if you need it)" % name)
    fact("env_login", login)
    fact("env_unknown", len(plan["unknown"]))

def main():
    command = sys.argv[1]
    if command == "scan":
        scan(sys.argv[2], sys.argv[3] == "1")
    elif command == "copy":
        copy(sys.argv[2], sys.argv[3], sys.argv[4] == "1", sys.argv[5] == "1", sys.argv[6] == "1", sys.argv[7] == "1")
    elif command == "envparse":
        envparse()
    elif command == "envwrite":
        envwrite(sys.argv[2], sys.argv[3] == "1")
    elif command == "setenv":
        # setenv FILE KEY VALUE: add KEY=VALUE to a Compose env file unless it already has a value
        path = sys.argv[2]
        ensure_file(path, path + ".example")
        if not existing_value(path, sys.argv[3]):
            append_line(path, "%s=%s" % (sys.argv[3], sys.argv[4]))
    else:
        sys.exit(2)

main()
'
helper() { "$PY" -I -c "$HELPER" "$@"; }
# the helper's own lines, and the facts (@name=value) it reports
fact_of() { printf '%s\n' "$1" | sed -n "s/^@$2=//p" | tail -n 1; }
shown_lines() { printf '%s\n' "$1" | grep -v '^@' || true; }

# --- the native install -------------------------------------------------------------------------------------------

say "Moving the native install into the container install (issue #233). $([ "$APPLY" = 1 ] && echo 'APPLY: this changes things.' || echo 'DRY RUN: nothing is changed.')"
say ""
say "What was found on this machine:"

[ -d "$NATIVE_DIR" ] || block "there is no native install at $NATIVE_DIR (pass --native-dir if it is somewhere else)."
say "  native install:  $NATIVE_DIR$([ -d "$NATIVE_DIR" ] || echo ' (not there)')"

# the env file belongs to root and the service group: read it as ourselves if we can, else with sudo (read only)
rd_cat() { if [ -r "$1" ]; then cat "$1"; else as_root cat "$1"; fi; }
if [ -e "$ENV_FILE" ]; then
  if [ -r "$ENV_FILE" ]; then say "  settings:        $ENV_FILE"; else say "  settings:        $ENV_FILE (readable only with sudo; it is read with sudo, read only)"; fi
else
  say "  settings:        no $ENV_FILE (nothing to carry over: no login, no AMI settings)"
fi

if [ -z "$NATIVE_DATA" ]; then
  if [ -e "$ENV_FILE" ]; then
    NATIVE_DATA="$(rd_cat "$ENV_FILE" | sed -n 's/^MOREOPENREPEATER_DATA_DIR=//p' | tail -n 1 | tr -d "\"'")"
  fi
  [ -n "$NATIVE_DATA" ] || NATIVE_DATA="$NATIVE_DIR/data"
fi
say "  native data:     $NATIVE_DATA"

# the service(s)
NATIVE_UNIT=""
for unit in moreopenrepeater moreopenrepeater-core; do
  has_file=no
  [ -f "$SYSTEMD/$unit.service" ] && has_file=yes
  active="$(systemctl is-active "$unit" 2>/dev/null || true)"
  enabled="$(systemctl is-enabled "$unit" 2>/dev/null || true)"
  if [ "$has_file" = yes ] || [ "$active" = active ]; then
    say "  service:         $unit ($active, $enabled)"
    if [ "$active" = active ] || [ "$enabled" = enabled ]; then NATIVE_UNIT="${NATIVE_UNIT:+$NATIVE_UNIT }$unit"; fi
  fi
done
if [ -z "$NATIVE_UNIT" ]; then
  say "  service:         no active or enabled moreopenrepeater unit (if it runs some other way, stop it yourself before --apply)"
  RESTORE_UNIT=moreopenrepeater
else
  RESTORE_UNIT="$NATIVE_UNIT"
fi
RUNNING_UNITS=""
for unit in $NATIVE_UNIT; do
  [ "$(systemctl is-active "$unit" 2>/dev/null || true)" = active ] && RUNNING_UNITS="${RUNNING_UNITS:+$RUNNING_UNITS }$unit"
done
if [ -z "$RUNNING_UNITS" ] && "$PGREP" -f 'moreopenrepeater-(api|core)' >/dev/null 2>&1; then
  block "a moreopenrepeater process is running outside systemd. Stop it first: it would hold the sound card and the PTT line."
fi

# the udev rule
if [ -f "$RULES/99-cm108.rules" ]; then say "  udev rule:       $RULES/99-cm108.rules (the native install's)"; else say "  udev rule:       no native rule found in $RULES"; fi
CONTAINER_RULE=no
[ -f "$RULES/61-moreopenrepeater-ptt.rules" ] && CONTAINER_RULE=yes
say "                   the container's PTT name rule (61-moreopenrepeater-ptt.rules): $CONTAINER_RULE"

# AllStar, DMR, SD protection, Tailscale
ALLSTAR_FOUND=0
if [ -f "$ASTERISK/rpt.conf" ]; then
  ALLSTAR_FOUND=1
  say "  AllStar node:    found ($ASTERISK/rpt.conf), asterisk is $(systemctl is-active asterisk 2>/dev/null || true)"
else
  say "  AllStar node:    none found ($ASTERISK/rpt.conf is not there)"
fi
DMR_FOUND=0
if [ -d "$ETC/moreopenrepeater/dmr" ] || [ -d "$OPT/Analog_Bridge" ] || [ -f "$ETC/systemd/system/analog_bridge.service.d/moreopenrepeater.conf" ]; then DMR_FOUND=1; fi
if [ "$DMR_FOUND" = 1 ]; then
  say "  DMR bridge:      found (DVSwitch on this machine)"
else
  say "  DMR bridge:      none found"
fi
PROTECT_SD=0
if [ -f "$ETC/systemd/journald.conf.d/60-moreopenrepeater.conf" ]; then
  PROTECT_SD=1
  say "  SD protection:   on (journald drop-in $ETC/systemd/journald.conf.d/60-moreopenrepeater.conf)"
else
  say "  SD protection:   off"
fi
if command -v tailscale >/dev/null 2>&1 || [ -S "$TS_DIR/tailscaled.sock" ]; then
  say "  Tailscale:       found on the host (it stays on the host; setup.sh shows its socket to the dashboard read-only)"
else
  say "  Tailscale:       not found"
fi

# Docker and the container stack
STACK_RUNNING=""
if ! command -v docker >/dev/null 2>&1; then
  block "docker is not installed (https://docs.docker.com/engine/install/). Nothing here installs it."
  say "  docker:          not installed"
elif ! docker compose version >/dev/null 2>&1; then
  block "'docker compose' is not available (the Docker Compose plugin is needed)."
  say "  docker:          installed, but no 'docker compose'"
else
  if STACK_RUNNING="$(docker compose ps -q 2>/dev/null)"; then
    if [ -n "$STACK_RUNNING" ]; then
      block "the container stack in $HERE is already running. Migrating into a live install could overwrite what it keeps. Stop it first (docker compose down, without -v) if this is the right thing."
      say "  docker:          the container stack is ALREADY RUNNING"
    else
      say "  docker:          ready, container stack not running"
    fi
  else
    block "docker could not be asked about the stack (is the Docker daemon running, and are you in the docker group?)."
    say "  docker:          could not be asked"
  fi
fi

# --- the target ---------------------------------------------------------------------------------------------------

ENV_TARGET="$(value .env MOR_DATA_DIR)"
if [ -n "$TARGET" ] && [ -n "$ENV_TARGET" ] && [ "$TARGET" != "$ENV_TARGET" ]; then
  block ".env already says MOR_DATA_DIR=$ENV_TARGET. Drop --target, or take that line out of .env."
fi
[ -n "$TARGET" ] || TARGET="$ENV_TARGET"
[ -n "$TARGET" ] || TARGET="$HERE/data"
case "$TARGET" in /*) ;; *) TARGET="$HERE/$TARGET" ;; esac
case "$TARGET" in
  *[!A-Za-z0-9._/+@:-]*) block "$TARGET has characters (spaces, quotes, \$) that don't survive an .env file. Pick a plainer folder with --target." ;;
esac
say "  target data:     $TARGET"

NATIVE_REAL="$(cd "$NATIVE_DATA" 2>/dev/null && pwd -P || echo "$NATIVE_DATA")"
TARGET_REAL="$(cd "$TARGET" 2>/dev/null && pwd -P || echo "$TARGET")"
case "$TARGET_REAL/" in
  "$NATIVE_REAL"/*) block "the target data folder is the native data folder (or inside it). Use a different --target: the native data must stay as it is, for the way back." ;;
esac
case "$NATIVE_REAL/" in
  "$TARGET_REAL"/*) block "the native data folder is inside the target. Use a different --target." ;;
esac
TARGET_STATE=empty
if [ -e "$TARGET" ]; then
  if [ ! -d "$TARGET" ]; then
    block "$TARGET exists and is not a folder."
    TARGET_STATE=blocked
  elif ! listing="$(ls -A "$TARGET" 2>/dev/null)"; then
    TARGET_STATE=unreadable
  elif [ -n "$(printf '%s\n' "$listing" | grep -v '^\.gitkeep$' || true)" ]; then
    TARGET_STATE=full
  fi
fi
if [ "$TARGET_STATE" = unreadable ]; then
  block "can't look inside $TARGET (the container owns it); treating it as not empty. Re-run with --into-existing if it should be filled."
elif [ "$TARGET_STATE" = full ]; then
  if [ "$INTO_EXISTING" = 1 ]; then
    say "                   it already holds files: --into-existing, so nothing is overwritten$([ "$FORCE" = 1 ] && echo ' except with --force (the old file is kept as NAME.before-migrate)')"
  else
    block "the target data folder $TARGET is not empty. A migration only fills an empty folder; pass --into-existing to add what is missing (it never overwrites a file without --force)."
  fi
fi

# --- the data and the settings (read only) --------------------------------------------------------------------------

say ""
say "The native data ($NATIVE_DATA), read only:"
READ_AS_ROOT=0
if [ -d "$NATIVE_DATA" ]; then
  for f in "$NATIVE_DATA" "$NATIVE_DATA/state.json" "$NATIVE_DATA/users.json" "$NATIVE_DATA/audio"; do
    if [ -e "$f" ] && [ ! -r "$f" ]; then READ_AS_ROOT=1; fi
  done
fi
[ "$READ_AS_ROOT" = 0 ] || say "  (these files belong to the moreopenrepeater user; they are read with sudo, read only)"
if [ "$READ_AS_ROOT" = 1 ]; then
  SCAN="$(as_root "$PY" -I -c "$HELPER" scan "$NATIVE_DATA" "$RECORDINGS")"
else
  SCAN="$(helper scan "$NATIVE_DATA" "$RECORDINGS")"
fi
shown_lines "$SCAN"
STATE_OK="$(fact_of "$SCAN" state_ok)"
CALLSIGN="$(fact_of "$SCAN" callsign)"
if [ "$STATE_OK" != 1 ]; then
  block "the native data is not usable: $(fact_of "$SCAN" state_why). The core will not start without a state.json that has a callsign."
fi
HINT_GPIO="$(fact_of "$SCAN" hint_gpio)"
HINT_LINK="$(fact_of "$SCAN" hint_link_radio)"
HINT_SERIALS="$(fact_of "$SCAN" hint_serials)"

say ""
say "The native settings ($ENV_FILE) and where each goes:"
ENV_PLAN=""
if [ -e "$ENV_FILE" ]; then
  ENV_PLAN="$(rd_cat "$ENV_FILE" | helper envparse)"
else
  ENV_PLAN='{"carry": [], "skip": [], "unknown": [], "login": "none", "keys": []}'
fi

# --- what would be handed to setup.sh -----------------------------------------------------------------------------

SETUP_ARGS=""
[ "$CONTAINER_RULE" = yes ] || SETUP_ARGS="$SETUP_ARGS --udev"
[ "$HINT_GPIO" != 1 ] || SETUP_ARGS="$SETUP_ARGS --gpio"
SERIAL_MANUAL=""
for dev in $HINT_SERIALS; do
  case "$dev" in
    *[!A-Za-z0-9._/+@:-]*) SERIAL_MANUAL="$SERIAL_MANUAL $dev" ;;
    "$DEV"/serial/by-id/*|"$DEV"/ttyUSB[0-9]*|"$DEV"/ttyACM[0-9]*)
      if [ -e "$dev" ]; then SETUP_ARGS="$SETUP_ARGS --serial $dev"; else SERIAL_MANUAL="$SERIAL_MANUAL $dev"; fi ;;
    *) SERIAL_MANUAL="$SERIAL_MANUAL $dev" ;;
  esac
done
[ "$HINT_LINK" != 1 ] || SETUP_ARGS="$SETUP_ARGS --no-start"
[ "$NO_SETUP" = 0 ] || SETUP_ARGS="$SETUP_ARGS --no-start"
SETUP_ARGS="${SETUP_ARGS# }"

say_plan() {
  say "  stop the native service:   sudo systemctl disable --now $RESTORE_UNIT"
  say "                             (undo: sudo systemctl enable --now $RESTORE_UNIT)"
  say "  station:                   ${CALLSIGN:-unknown} (from the native state.json)"
  say "  copy the data to:          $TARGET (as uid 999, files 0600 for accounts, settings and databases)"
  say "  write the settings to:     $HERE/api.env, core.env and .env (existing values kept)"
  if [ "$NO_SETUP" = 1 ]; then
    say "  then stop (--no-setup); you run:  ./setup.sh ${SETUP_ARGS%--no-start}"
  else
    say "  then hand over to:         ./setup.sh${SETUP_ARGS:+ $SETUP_ARGS}"
  fi
}

print_not_moved() {
  say "Not moved, on purpose:"
  if [ "$ALLSTAR_FOUND" = 1 ]; then
    say "  AllStar: the node on this machine ($ASTERISK) is NOT touched and stays on the host. The container install runs its own node."
    say "    To bring the node's number, callsign, registration and links over (it reads $ASTERISK, read only):"
    say "      1. stop the host's: sudo systemctl disable --now asterisk     (undo: sudo systemctl enable --now asterisk)"
    say "         Two nodes with one number and the same IAX port (4569) must not run together."
    say "      2. ./setup.sh --import-allstar $ASTERISK"
    say "      3. open the dashboard's AllStarLink page and click Import node (docs/allstar.md, Importing a node)."
    say "    Until then the repeater runs with no AllStar link."
  fi
  if [ "$DMR_FOUND" = 1 ]; then
    say "  DMR: DVSwitch (Analog_Bridge, MMDVM_Bridge) on this machine has NO importer. Its settings are not read and not copied."
    say "    By hand: stop it (sudo systemctl disable --now analog_bridge mmdvm_bridge; undo with enable --now), then"
    say "    ./setup.sh --dmr, open the dashboard's DMR page and enter the callsign, DMR Id, network and password again (docs/docker.md, The DMR container)."
    say "    Look them up first in /etc/moreopenrepeater/dmr/*.ini, which this script did not open."
  fi
  if [ "$PROTECT_SD" = 1 ]; then
    say "  SD protection: the native journal and log drop-in stay as they are. The container counterpart is compose.protect-sd.yaml (docs/docker.md, Sparing the SD card)."
  fi
  if [ -n "$SERIAL_MANUAL" ]; then
    say "  Serial PTT or COS devices the saved configuration uses but this script did not pass on:$SERIAL_MANUAL"
    say "    Plug them in and run ./setup.sh --serial /dev/serial/by-id/NAME for each."
  fi
  if [ "$HINT_LINK" = 1 ]; then
    say "  Link radio: the saved configuration has one. Its USB interface needs its own name:"
    say "    ./setup.sh --list-interfaces       then      ./setup.sh --link-radio ID"
    say "    The containers were not started until you do (setup.sh --link-radio starts them)."
  fi
}

# --- dry run ends here --------------------------------------------------------------------------------------------

if [ "$APPLY" = 0 ]; then
  # the env plan, as the dry run: a temporary folder stands in so nothing is written
  printf '%s' "$ENV_PLAN" | "$PY" -I -c "$HELPER" envwrite "$HERE" 0 | grep -v '^@' || true
  say ""
  say "What --apply would do, in this order:"
  say_plan
  say ""
  print_not_moved
  say ""
  if [ -n "$BLOCK" ]; then
    printf 'It would REFUSE, and change nothing, because:%s\n' "$BLOCK"
    exit 2
  fi
  say "Nothing was changed. If the plan is right: ./migrate-from-native.sh --apply$([ "$INTO_EXISTING" = 1 ] && echo ' --into-existing')$([ "$FORCE" = 1 ] && echo ' --force')$([ "$RECORDINGS" = 1 ] && echo ' --recordings')"
  say "Going back is easy: the native data is only ever read (docs/docker.md, Moving from the native install)."
  exit 0
fi

# --- apply --------------------------------------------------------------------------------------------------------

if [ -n "$BLOCK" ]; then
  printf 'migrate: refusing to start, and nothing was changed, because:%s\n' "$BLOCK" >&2
  exit 2
fi
printf '%s' "$ENV_PLAN" | "$PY" -I -c "$HELPER" envwrite "$HERE" 0 | grep -v '^@' || true
say ""

# 1. stop the native service. The data must not change while it is copied, and the sound card and PTT line are
#    claimed by one of the two only.
if [ -n "$RUNNING_UNITS" ]; then
  say "Stopping the native service now. The repeater is OFF THE AIR until the container install is up."
  say "  Undo: sudo systemctl enable --now $RESTORE_UNIT"
  for unit in $RUNNING_UNITS; do as_root systemctl disable --now "$unit"; done
  for unit in $RUNNING_UNITS; do
    [ "$(systemctl is-active "$unit" 2>/dev/null || true)" != active ] || die "$unit is still running after it was told to stop. Nothing has been copied. Undo: sudo systemctl enable --now $RESTORE_UNIT"
  done
else
  say "The native service is not running, so it was not stopped."
fi

# 2. the data
say ""
say "Copying the data (as root, so the files can be read and given to uid 999; the originals are only read):"
COPY_ARGS="$NATIVE_DATA $TARGET $RECORDINGS $CHOWN $FORCE $INTO_EXISTING"
set +e
# shellcheck disable=SC2086
COPY="$(as_root "$PY" -I -c "$HELPER" copy $COPY_ARGS)"
COPY_STATUS=$?
set -e
shown_lines "$COPY"
if [ "$COPY_STATUS" != 0 ]; then
  say ""
  say "Stopped. The native data was only read, and what this run wrote was removed. The native service is stopped:"
  say "  to put it back:  sudo systemctl enable --now $RESTORE_UNIT"
  exit 1
fi

# 3. the settings
say ""
say "Writing the settings into $HERE:"
if [ ! -f .env ]; then cp .env.example .env; chmod 600 .env; fi
helper setenv .env MOR_DATA_DIR "$TARGET"
printf '%s' "$ENV_PLAN" | "$PY" -I -c "$HELPER" envwrite "$HERE" 1 | grep -v '^@' || true
chmod 600 .env
[ ! -f api.env ] || chmod 600 api.env
[ ! -f core.env ] || chmod 600 core.env

# 4. setup.sh
say ""
if [ "$NO_SETUP" = 1 ]; then
  say "Stopped before setup.sh, as asked (--no-setup). Next:  ./setup.sh ${SETUP_ARGS%--no-start}"
else
  say "Handing over to setup.sh${SETUP_ARGS:+ ($SETUP_ARGS)}. It keeps the saved configuration that was just copied and the login in api.env,"
  say "starts the containers (the first build takes a few minutes), and prints the dashboard addresses."
  say "--------------------------------------------------------------------------------"
  set +e
  # shellcheck disable=SC2086
  sh "$SETUP" $SETUP_ARGS
  SETUP_STATUS=$?
  set -e
  say "--------------------------------------------------------------------------------"
  if [ "$SETUP_STATUS" != 0 ]; then
    say "setup.sh stopped with status $SETUP_STATUS. The data and settings are in place; read what it said above and run it again:"
    say "  ./setup.sh${SETUP_ARGS:+ $SETUP_ARGS}"
    say "The native service stays stopped. To go back instead:"
    say "  cd $HERE && docker compose down      (no -v)"
    say "  sudo systemctl enable --now $RESTORE_UNIT"
    exit 1
  fi
fi

# --- the checklist ------------------------------------------------------------------------------------------------

say ""
say "================================================================================"
say "Checklist (this script can not hear the radio; none of the last part is checked)"
say "================================================================================"
say "Copied into $TARGET: see the list above (a line says 'kept' where a file was already there)."
say "Not copied: what the 'left behind' and 'not carried' lines above say, and the AllStar and DMR items below."
say ""
print_not_moved
say ""
say "Sign in with the same admin login you had, unless the list above said a new one was generated."
say ""
say "ROLLBACK (the native data was only read, so this is all it takes):"
say "  cd $HERE && docker compose down                   (no -v: that would delete the container's volumes)"
say "                                                    (name --profile allstar, dmr, winlink, time too if you started those)"
say "  sudo systemctl enable --now $RESTORE_UNIT"
say "  The container install's newer state.json fields are ignored by the native version (it drops fields it does not know,"
say "  src/api/service/snapshot.py), and it reads its own folder anyway. Changes you make in the container's dashboard stay"
say "  in $TARGET; they do not appear in the native install. To carry some back, use the dashboard's backup export and restore."
say ""
say "STILL TO DO BY YOU, ON THE AIR (nothing here has proved any of it):"
say "  [ ] the dashboard answers and shows the right callsign and the core healthy"
say "  [ ] the PTT interface is found: ls -l /dev/moreopenrepeater-ptt  (./setup.sh --udev if it is missing)"
say "  [ ] key the repeater from a radio and check it keys, repeats and drops, and the courtesy tone and hang time are right"
say "  [ ] wait for the identification and listen to it (callsign and timing)"
say "  [ ] check the audio levels (deviation on the transmitter, the dashboard's Audio page) against what you had"
say "  [ ] time-out timer, GPIO or serial lines, link radio, AllStar and DMR if you use them"
say "  [ ] watch it for a day before you delete anything from the native install"
exit 0
