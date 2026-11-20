#!/bin/sh
# The container install: from a fresh checkout to a dashboard you can sign in to (docs/docker.md, Install).
#
#   cd docker && ./setup.sh --callsign W1AW
#
# What it does, and only what is missing (run it again any time; it never changes a value you have set):
#   - writes .env, api.env, core.env (and allstar.env with --allstar) from the .example files, readable by you only
#   - seeds the saved configuration, which the core will not start without: a callsign, and the rest in the dashboard
#   - turns sign-in on with an admin password it generates and prints ONCE, then, and only then, publishes the
#     dashboard on every network interface, so it answers on localhost, on the Pi's LAN address and on its
#     Tailscale address. Without sign-in the dashboard is open and can key the transmitter, so it stays on the loopback.
#   - starts the containers and waits until they are healthy
#
#   --callsign CALL   the station's callsign (asked for if this is a first install and you are at a terminal)
#   --localhost       publish the dashboard on the loopback only (a reverse proxy or `tailscale serve` on this host)
#   --allstar         also set up and start the AllStarLink node container (its management secret is generated)
#   --import-allstar DIR  an AllStarLink node already on this machine: DIR is its /etc/asterisk. The AllStar page offers to bring
#                     the node over (it is shown first, and nothing is written until you confirm). Implies --allstar.
#   --dmr             also start the DMR bridge container (the network, DMR Id and password are set in the dashboard)
#   --udev            install the udev rule that gives the PTT interface a stable name (needs sudo)
#   --usb-speed       Raspberry Pi 3 Model B+ only: hold USB to full speed (dwc_otg.speed=1 in cmdline.txt, backed up first),
#                     which stops the AIOC's audio glitches under load. Needs sudo and a reboot, which it does not do.
#   --no-start        write the settings, start nothing
#
# Later updates are ./update.sh (pull, rebuild, restart).
set -eu

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

CALLSIGN=""
LOCALHOST=0
ALLSTAR=0
IMPORT_ALLSTAR=""
DMR=0
UDEV=0
USB_SPEED=0
START=1
while [ $# -gt 0 ]; do
  case "$1" in
    --callsign) CALLSIGN="${2:-}"; shift ;;
    --localhost) LOCALHOST=1 ;;
    --allstar) ALLSTAR=1 ;;
    --import-allstar) IMPORT_ALLSTAR="${2:-}"; ALLSTAR=1; [ -n "$IMPORT_ALLSTAR" ] || { echo "setup: --import-allstar needs the folder, for example --import-allstar /etc/asterisk" >&2; exit 2; }; shift ;;
    --dmr) DMR=1 ;;
    --udev) UDEV=1 ;;
    --usb-speed) USB_SPEED=1 ;;
    --no-start) START=0 ;;
    -h|--help) sed -n '2,23p' "$HERE/setup.sh" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "setup: unknown option $1 (see --help)" >&2; exit 2 ;;
  esac
  shift
done

say() { printf '%s\n' "$*"; }
die() { printf 'setup: %s\n' "$*" >&2; exit 2; }

# --- helpers over the env files ----------------------------------------------------------------------------------

# value FILE NAME: the value of an uncommented NAME= line, or nothing
value() { [ -f "$1" ] && sed -n "s/^$2=//p" "$1" | tail -n 1 || true; }
# set_value FILE NAME VALUE: add NAME=VALUE if no uncommented line for NAME exists
set_value() {
  [ -n "$(value "$1" "$2")" ] || printf '%s=%s\n' "$2" "$3" >> "$1"
}
# force_value FILE NAME VALUE: replace an uncommented NAME= line, or add it
force_value() {
  if grep -q "^$2=" "$1" 2>/dev/null; then
    sed "s|^$2=.*|$2=$3|" "$1" > "$1.new" && mv "$1.new" "$1"
  else
    printf '%s=%s\n' "$2" "$3" >> "$1"
  fi
}
random() { LC_ALL=C tr -dc "$1" < /dev/urandom | head -c "$2"; echo; }
from_example() { # NAME: create NAME from NAME.example, for the owner only
  if [ ! -f "$1" ]; then
    cp "$1.example" "$1"
    chmod 600 "$1"
    say "  wrote $1"
  fi
}

say "Setting up the repeater containers in $HERE"

# --- prerequisites ------------------------------------------------------------------------------------------------

command -v docker >/dev/null 2>&1 || die "docker is not installed. See https://docs.docker.com/engine/install/"
docker compose version >/dev/null 2>&1 || die "'docker compose' is not available (the Docker Compose plugin is needed)."
if [ "$(systemctl is-active moreopenrepeater 2>/dev/null || true)" = "active" ]; then
  die "the moreopenrepeater service is running. Stop it first (sudo systemctl disable --now moreopenrepeater): it and the core would claim the same sound card and PTT line."
fi

# --- settings files -----------------------------------------------------------------------------------------------

for f in .env api.env core.env; do from_example "$f"; done
chmod 600 api.env core.env
[ "$ALLSTAR" = 0 ] || { from_example allstar.env; chmod 600 allstar.env; }

# --- the saved configuration the core will not start without ---------------------------------------------------

DATA_DIR="$(value .env MOR_DATA_DIR)"
if [ -z "$DATA_DIR" ]; then
  DATA_DIR="$HERE/data"
  printf 'MOR_DATA_DIR=%s\n' "$DATA_DIR" >> .env
fi
if [ ! -f "$DATA_DIR/state.json" ]; then
  if [ -z "$CALLSIGN" ] && [ -t 0 ]; then
    printf 'Your station callsign: '
    read -r CALLSIGN
  fi
  CALLSIGN="$(printf '%s' "$CALLSIGN" | tr '[:lower:]' '[:upper:]')"
  [ -n "$CALLSIGN" ] || die "a first install needs your callsign: ./setup.sh --callsign W1AW (the core will not run without one; the rest is set in the dashboard)."
  printf '%s' "$CALLSIGN" | grep -Eq '^[A-Z0-9]([A-Z0-9/-]{1,14})[A-Z0-9]$' || die "'$CALLSIGN' does not look like a callsign."
  mkdir -p "$DATA_DIR"
  printf '{"config": {"callsign": "%s"}}\n' "$CALLSIGN" > "$DATA_DIR/state.json"
  say "  seeded $DATA_DIR/state.json for $CALLSIGN"
fi
# the containers run as uid 999 and own the data folder
if [ "${MOR_SETUP_CHOWN:-1}" = 1 ] && [ "$(stat -c %u "$DATA_DIR" 2>/dev/null || echo 999)" != 999 ]; then
  if [ "$(id -u)" = 0 ]; then chown -R 999:999 "$DATA_DIR"; else sudo chown -R 999:999 "$DATA_DIR"; fi
fi

# --- sign-in, then and only then the network ---------------------------------------------------------------------

GENERATED=""
USER_SET="$(value api.env MOREOPENREPEATER_AUTH_USER)"
PASS_SET="$(value api.env MOREOPENREPEATER_AUTH_PASSWORD)"
HALF=0
if [ -n "$USER_SET" ]; then
  [ -n "$PASS_SET" ] || HALF=1
elif [ -n "$PASS_SET" ]; then
  HALF=1
fi
[ "$HALF" = 0 ] || die "api.env sets only one of MOREOPENREPEATER_AUTH_USER and MOREOPENREPEATER_AUTH_PASSWORD; the dashboard refuses to start with half a login. Set both or neither."
if [ -z "$USER_SET" ]; then
  GENERATED="$(random 'A-Za-z0-9' 20)"
  printf 'MOREOPENREPEATER_AUTH_USER=admin\nMOREOPENREPEATER_AUTH_PASSWORD=%s\n' "$GENERATED" >> api.env
  USER_SET=admin
fi
if [ "$LOCALHOST" = 1 ]; then
  force_value .env MOR_API_HOST 127.0.0.1
else
  set_value .env MOR_API_HOST 0.0.0.0   # sign-in is on (above), so the dashboard may answer on every interface
fi

# --- Tailscale's socket, so the Remote page can see the host's Tailscale ------------------------------------------------

# The API container has no tailscale command and must not get one. tailscaled's socket folder is shown to it read-only
# instead (compose.yaml). The folder has to exist on the host before Docker starts, or Docker makes it as root; so it is only
# pointed at when the socket is there, and a machine without Tailscale keeps the empty folder in this repo. A value you set
# yourself is kept (set_value). MOR_TAILSCALE_SOCKET_DIR is only a hook for the tests.
TS_DIR="${MOR_TAILSCALE_SOCKET_DIR:-/var/run/tailscale}"
if [ -S "$TS_DIR/tailscaled.sock" ]; then
  set_value .env MOR_TAILSCALE_DIR "$TS_DIR"
fi

# --- the host's kernel log, for the support bundle ------------------------------------------------------------------

# The core gets /dev/kmsg read-only (compose.yaml), so the support bundle can show a USB drop. Compose maps /dev/null in its
# place unless MOR_KMSG_DEVICE says otherwise, so this is only turned on when the host has /dev/kmsg and the current user can
# open it (a host with kernel.dmesg_restrict=1 refuses everyone but root, and the core is not root). A value you set yourself,
# /dev/null included, is kept (set_value). MOR_KMSG_FILE is only a hook for the tests.
KMSG="${MOR_KMSG_FILE:-/dev/kmsg}"
if [ -e "$KMSG" ] && [ ! -d "$KMSG" ]; then
  KMSG_BEFORE="$(value .env MOR_KMSG_DEVICE)"
  # The subshell: a failed redirection would end this script in some shells.
  if (: < "$KMSG") 2>/dev/null; then
    set_value .env MOR_KMSG_DEVICE "$KMSG"
    [ -n "$KMSG_BEFORE" ] || say "  the core can read the host's kernel log, read-only (for the support bundle). To turn that off: MOR_KMSG_DEVICE=/dev/null in .env"
  elif [ -z "$KMSG_BEFORE" ]; then
    say "  NOTE: $KMSG is not readable by $(id -un), so the core is not given the host's kernel log (the support bundle will say so)."
  fi
fi

# --- the AllStar node ---------------------------------------------------------------------------------------------

if [ "$ALLSTAR" = 1 ]; then
  [ -n "$(value allstar.env MOR_AMI_SECRET)" ] || printf 'MOR_AMI_SECRET=%s\n' "$(random 'a-f0-9' 32)" >> allstar.env
  SECRET="$(value allstar.env MOR_AMI_SECRET)"
  for f in api.env core.env; do
    set_value "$f" MOREOPENREPEATER_AMI_HOST allstar
    set_value "$f" MOREOPENREPEATER_AMI_SECRET "$SECRET"
  done
fi

# --- an AllStar node already on this machine --------------------------------------------------------------------

# The folder is mounted read-only into the API container (compose.yaml), which only reads it, when the AllStar page's
# import is used. Without this the mount is the empty docker/empty folder, so nothing changes for an install without one.
if [ -n "$IMPORT_ALLSTAR" ]; then
  [ -d "$IMPORT_ALLSTAR" ] || die "--import-allstar: $IMPORT_ALLSTAR is not a folder."
  IMPORT_DIR="$(cd "$IMPORT_ALLSTAR" && pwd)"
  [ -e "$IMPORT_DIR/rpt.conf" ] || die "--import-allstar: there is no rpt.conf in $IMPORT_DIR (give the folder that holds the node's configuration, usually /etc/asterisk)."
  case "$IMPORT_DIR" in
    *[!A-Za-z0-9._/+@:-]*) die "--import-allstar: $IMPORT_DIR has characters (spaces, quotes, \$) that don't survive an .env file; copy the folder to a plainer path." ;;
  esac
  CURRENT_IMPORT="$(value .env MOR_ALLSTAR_IMPORT_DIR)"
  if [ -n "$CURRENT_IMPORT" ] && [ "$CURRENT_IMPORT" != "$IMPORT_DIR" ]; then
    say "  .env already sets MOR_ALLSTAR_IMPORT_DIR=$CURRENT_IMPORT; left as it is (edit .env to change it)."
  else
    set_value .env MOR_ALLSTAR_IMPORT_DIR "$IMPORT_DIR"
    say "  the AllStar page will offer to import the node in $IMPORT_DIR (read-only)"
  fi
  # A host node's files are normally readable by root and the asterisk group only, and the API container runs as user 999.
  if [ -z "$(find "$IMPORT_DIR/rpt.conf" -perm -004 2>/dev/null)" ]; then
    say ""
    say "  NOTE: $IMPORT_DIR/rpt.conf is not readable by other users, and the API container is not root. If the AllStar page"
    say "  says it can't read a file, give it a private copy it can read, then point --import-allstar at the copy:"
    say "    sudo cp -a $IMPORT_DIR /var/tmp/asterisk-import && sudo chown -R 999:999 /var/tmp/asterisk-import && sudo chmod -R go-rwx /var/tmp/asterisk-import"
    say "  Delete the copy when the import is done: it holds the node's password."
  fi
fi

# --- the PTT interface's stable name ------------------------------------------------------------------------------

if [ "$UDEV" = 1 ]; then
  sudo cp udev/61-moreopenrepeater-ptt.rules /etc/udev/rules.d/
  sudo udevadm control --reload-rules
  sudo udevadm trigger --subsystem-match=hidraw --action=add
fi
PTT="$(value .env MOR_PTT_HIDRAW)"
if [ ! -e "${PTT:-/dev/moreopenrepeater-ptt}" ]; then
  say ""
  say "  NOTE: ${PTT:-/dev/moreopenrepeater-ptt} does not exist, and the core will not start without it."
  say "  Plug in the PTT interface (AIOC or CM108) and install the rule that names it:  ./setup.sh --udev"
  START=0
fi

# --- a Pi 3 Model B+: USB at full speed ---------------------------------------------------------------------------

# The AIOC is a full-speed device behind the 3B+'s high-speed hub, so each transfer is a split transaction that the USB
# controller must time exactly, and other processes' memory traffic makes it miss. Holding the controller to full speed
# removes the split transactions (docs/docker.md, Audio glitches). It costs: every USB device on the Pi, wired Ethernet
# included, runs at 12 Mbit/s. So it is only ever done when asked, and it is a boot setting: it takes a reboot, which
# this script never does.
as_root() { if [ "$(id -u)" = 0 ]; then "$@"; else sudo "$@"; fi; }
MODEL="$(tr -d '\0' < "${MOR_PI_MODEL_FILE:-/proc/device-tree/model}" 2>/dev/null || true)"
CMDLINE="${MOR_CMDLINE_FILE:-}"
if [ -z "$CMDLINE" ]; then
  for candidate in /boot/firmware/cmdline.txt /boot/cmdline.txt; do
    if [ -f "$candidate" ]; then CMDLINE="$candidate"; break; fi
  done
fi
case "$MODEL" in
  *"Raspberry Pi 3 Model B Plus"*)
    if grep -q 'dwc_otg\.speed=1' "${MOR_PROC_CMDLINE:-/proc/cmdline}" 2>/dev/null; then
      say "  USB is already held to full speed (dwc_otg.speed=1)."
    elif [ "$USB_SPEED" = 1 ]; then
      [ -n "$CMDLINE" ] && [ -f "$CMDLINE" ] || die "can't find the Pi's cmdline.txt (looked in /boot/firmware and /boot)."
      if grep -q 'dwc_otg\.speed=1' "$CMDLINE"; then
        say "  $CMDLINE already has dwc_otg.speed=1; it takes effect at the next reboot."
      else
        [ "$(grep -c '' "$CMDLINE")" = 1 ] || die "$CMDLINE is not the single line the kernel expects; not changing it."
        [ -e "$CMDLINE.before-speed1" ] || as_root cp "$CMDLINE" "$CMDLINE.before-speed1"
        NEW="$(head -n 1 "$CMDLINE" | sed 's/[[:space:]]*$//') dwc_otg.speed=1"
        printf '%s\n' "$NEW" | as_root tee "$CMDLINE" >/dev/null
        grep -q 'dwc_otg\.speed=1' "$CMDLINE" && [ "$(grep -c '' "$CMDLINE")" = 1 ] || {
          as_root cp "$CMDLINE.before-speed1" "$CMDLINE"
          die "couldn't write $CMDLINE; put the original back from $CMDLINE.before-speed1."
        }
        say "  added dwc_otg.speed=1 to $CMDLINE (the original is $CMDLINE.before-speed1)."
        say "  Reboot the Pi for it to take effect. Every USB device, wired Ethernet included, then runs at 12 Mbit/s."
      fi
    else
      say ""
      say "  NOTE: this is a Raspberry Pi 3 Model B+. With the AIOC on it, other processes can make the USB controller miss audio"
      say "  (\"audio glitch\" in the log). Holding USB to full speed fixes that: ./setup.sh --usb-speed, then reboot."
      say "  It also limits wired Ethernet to 12 Mbit/s, which is why it is not done unasked."
    fi
    ;;
  *)
    [ "$USB_SPEED" = 0 ] || die "--usb-speed is only for a Raspberry Pi 3 Model B+ (this is: ${MODEL:-not a Raspberry Pi})."
    ;;
esac

# --- which version this is ---------------------------------------------------------------------------------------

# The API image has no git, so the commit goes in as a build argument from .env (docker/update.sh does the same on every
# update). The dashboard's Updates page shows it. Not a git checkout (a downloaded copy): it says "unknown" there.
sh ./update.sh --record-only 2>/dev/null || say "  (couldn't note which version this is; the Updates page will say unknown until ./update.sh runs in a git checkout)"

# --- start -------------------------------------------------------------------------------------------------------

if [ "$START" = 1 ]; then
  PROFILE=""
  [ "$ALLSTAR" = 0 ] || PROFILE="--profile allstar"
  [ "$DMR" = 0 ] || PROFILE="$PROFILE --profile dmr"
  say "  starting the containers (the first build takes a few minutes)"
  # shellcheck disable=SC2086
  docker compose $PROFILE up -d --build
  waited=0
  while [ "$waited" -lt "${MOR_SETUP_WAIT:-240}" ]; do
    # shellcheck disable=SC2086
    status="$(docker compose $PROFILE ps --format '{{.Name}} {{.Status}}')"
    if ! printf '%s\n' "$status" | grep -v '(healthy)' | grep -q .; then break; fi
    sleep 5
    waited=$((waited + 5))
  done
  say "$status"
fi

# --- what to open -------------------------------------------------------------------------------------------------

PORT="$(value .env MOR_API_PORT)"
PORT="${PORT:-8000}"
HOST="$(value .env MOR_API_HOST)"
say ""
say "Dashboard:"
say "  on this machine:   http://localhost:$PORT"
if [ "$HOST" = "0.0.0.0" ]; then
  # The machine's own addresses, by interface so Docker's bridges (docker0, br-...) and Tailscale's are left out: they are
  # not where you would open this from. Without `ip`, every address that is not IPv6 or in Tailscale's range.
  if command -v ip >/dev/null 2>&1; then
    addresses="$(ip -4 -o addr show scope global 2>/dev/null | awk '$2 !~ /^(docker|br-|veth|virbr|cni|flannel|tailscale|lo$)/ {split($4, a, "/"); print a[1]}')"
  else
    addresses="$(hostname -I 2>/dev/null || true)"
  fi
  for ip in $addresses; do
    case "$ip" in
      *:*|100.6[4-9].*|100.[7-9]?.*|100.1[01]?.*|100.12[0-7].*) ;;  # not IPv6, not Tailscale's
      *) say "  on your network:   http://$ip:$PORT" ;;
    esac
  done
  if command -v tailscale >/dev/null 2>&1; then
    TS="$(tailscale ip -4 2>/dev/null | head -n 1 || true)"
    [ -z "$TS" ] || say "  over Tailscale:    http://$TS:$PORT"
  else
    say "  over Tailscale:    install Tailscale on this machine and it answers on its Tailscale address too"
  fi
else
  say "  (loopback only: use \`tailscale serve --bg $PORT\` or a reverse proxy on this machine to reach it from elsewhere)"
fi
if [ -n "$GENERATED" ]; then
  say ""
  say "Sign in as:  $USER_SET  /  $GENERATED"
  say "This is the only time the password is shown. It is saved in $HERE/api.env (readable by you only)."
fi
