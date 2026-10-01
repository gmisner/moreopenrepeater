#!/usr/bin/env bash
# Installs (or updates) moreopenrepeater on a Raspberry Pi / Debian system:
#
#   curl -fsSL https://raw.githubusercontent.com/gmisner/moreopenrepeater/main/scripts/install-pi.sh | sudo bash
#
# Safe to run again: it updates the checkout and dependencies and restarts
# the service, but never overwrites /etc/moreopenrepeater/env. What it does
# is the manual procedure in docs/raspberry-pi.md. The dashboard's Updates
# page runs it too (through scripts/update.sh).
#
# Options (append after `sudo bash -s --` when piping):
#   --channel stable|beta|dev
#              which releases to follow (default: the one chosen before, or
#              stable); see "Updates" in docs/raspberry-pi.md
#   --lan      listen on all interfaces instead of only the Pi itself
#              (first install only; see "Security" in docs/raspberry-pi.md)
#   --allstar  also install AllStarLink (ASL3) for linking and autopatch, and
#              connect the controller to it (Debian 12 or 13; docs/allstar.md)
#
# Environment overrides: REPO_URL, BRANCH (instead of the channel's branch),
# INSTALL_DIR.
#
# It runs in two parts: the first fetches the code, then hands over to the
# fetched copy of this script (--fetched), so an update always installs with
# the new version's steps.
set -euo pipefail
umask 022  # nothing it writes may be writable by the service user

REPO_URL="${REPO_URL:-https://github.com/gmisner/moreopenrepeater.git}"
INSTALL_DIR="${INSTALL_DIR:-/opt/moreopenrepeater}"
SERVICE_USER=moreopenrepeater
ENV_DIR=/etc/moreopenrepeater
ENV_FILE="$ENV_DIR/env"
LISTEN_LAN=0
ALLSTAR=0
CHANNEL=""
FETCHED=0
ASTERISK_DIR=/etc/asterisk
AMI_USER=moreopenrepeater

step() { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --lan) LISTEN_LAN=1 ;;
    --allstar) ALLSTAR=1 ;;
    --channel) CHANNEL="${2:-}"; shift ;;
    --channel=*) CHANNEL="${1#--channel=}" ;;
    --fetched) FETCHED=1 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

# Sets KEY=VALUE in the env file, uncommenting the line if it's there.
set_env() {
  local key="$1" value="$2"
  if grep -qE "^#?${key}=" "$ENV_FILE"; then
    sed -i -E "s|^#?${key}=.*|${key}=${value}|" "$ENV_FILE"
  else
    printf '%s=%s\n' "$key" "$value" >>"$ENV_FILE"
  fi
}

env_value() { sed -nE "s/^$1=(.*)/\1/p" "$ENV_FILE" | tail -n 1; }

# ASL3's apt repository package for this Debian release.
asl_repo_package() {
  local codename
  codename="$(sed -nE 's/^VERSION_CODENAME="?([^"]*)"?$/\1/p' /etc/os-release)"
  case "$codename" in
    bookworm) echo "asl-apt-repos.deb12_all.deb" ;;
    trixie) echo "asl-apt-repos.deb13_all.deb" ;;
    *) fail "AllStarLink (ASL3) packages are for Debian 12 (bookworm) and 13 (trixie), not '${codename:-this system}'." ;;
  esac
}

install_allstar() {
  step "Installing AllStarLink (ASL3)"
  if ! dpkg -s asl-apt-repos >/dev/null 2>&1; then
    local repo_deb
    repo_deb="$(asl_repo_package)"
    curl -fsSL -o "/tmp/$repo_deb" "https://repo.allstarlink.org/public/$repo_deb"
    dpkg -i "/tmp/$repo_deb"
    rm -f "/tmp/$repo_deb"
    apt-get update -q
  fi
  apt-get install -y -q asl3

  step "Letting moreopenrepeater set up the node"
  # It edits the node's lines in rpt.conf as text (see docs/allstar.md).
  usermod -aG asterisk "$SERVICE_USER"
  local file
  for file in rpt.conf modules.conf echolink.conf; do
    if [ -f "$ASTERISK_DIR/$file" ]; then chmod g+w "$ASTERISK_DIR/$file"; fi
  done

  # An AMI login of its own, usable only from this machine.
  local manager="$ASTERISK_DIR/manager.conf" secret
  secret="$(awk -v user="[$AMI_USER]" '$0 == user {found = 1; next} /^\[/ {found = 0} found && $1 == "secret" {print $3}' "$manager")"
  if [ -z "$secret" ]; then
    secret="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
    printf '\n[%s]\nsecret = %s\ndeny = 0.0.0.0/0.0.0.0\npermit = 127.0.0.1/255.255.255.255\nread = all\nwrite = all\n' \
      "$AMI_USER" "$secret" >>"$manager"
    if systemctl is-active -q asterisk; then asterisk -rx "manager reload" >/dev/null; fi
  fi
  if [ -z "$(env_value MOREOPENREPEATER_AMI_HOST)" ]; then
    set_env MOREOPENREPEATER_AMI_HOST 127.0.0.1
    set_env MOREOPENREPEATER_AMI_USER "$AMI_USER"
    set_env MOREOPENREPEATER_AMI_SECRET "$secret"
  fi
}

[ "$(id -u)" -eq 0 ] || fail "run this with sudo."
if [ "$ALLSTAR" -eq 1 ]; then asl_repo_package >/dev/null; fi
command -v apt-get >/dev/null || fail "this installer needs a Debian-based system (Raspberry Pi OS, Debian, Ubuntu)."

if [ -z "$CHANNEL" ] && [ -f "$ENV_FILE" ]; then CHANNEL="$(env_value MOREOPENREPEATER_UPDATE_CHANNEL)"; fi
CHANNEL="${CHANNEL:-stable}"
case "$CHANNEL" in
  stable) CHANNEL_BRANCH=stable ;;
  beta) CHANNEL_BRANCH=beta ;;
  dev) CHANNEL_BRANCH=main ;;
  *) fail "unknown channel '$CHANNEL' (choose stable, beta or dev)." ;;
esac

if [ "$FETCHED" -eq 0 ]; then
  BRANCH="${BRANCH:-$CHANNEL_BRANCH}"
  if ! command -v git >/dev/null; then
    step "Installing git"
    apt-get update -q
    apt-get install -y -q --no-install-recommends git
  fi

  step "Fetching moreopenrepeater ($CHANNEL: $BRANCH) into $INSTALL_DIR"
  if [ -d "$INSTALL_DIR/.git" ]; then
    # The code belongs to root (earlier versions gave it to the service
    # user), so the dashboard can't change what the updater runs as root.
    find "$INSTALL_DIR" -path "$INSTALL_DIR/data" -prune -o \( ! -user root -o ! -group root \) -exec chown -h root:root {} +
    find "$INSTALL_DIR" -path "$INSTALL_DIR/data" -prune -o ! -type l -perm /022 -exec chmod go-w {} +
    git -C "$INSTALL_DIR" fetch -q origin "$BRANCH"
    git -C "$INSTALL_DIR" checkout -q -f -B "$BRANCH" FETCH_HEAD
  else
    if [ -e "$INSTALL_DIR" ] && [ -n "$(ls -A "$INSTALL_DIR")" ]; then
      fail "$INSTALL_DIR exists and isn't a moreopenrepeater checkout; move it aside first."
    fi
    git clone -q --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
  fi

  pass=(--fetched --channel "$CHANNEL")
  if [ "$LISTEN_LAN" -eq 1 ]; then pass+=(--lan); fi
  if [ "$ALLSTAR" -eq 1 ]; then pass+=(--allstar); fi
  exec bash "$INSTALL_DIR/scripts/install-pi.sh" "${pass[@]}"
fi

step "Installing system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q --no-install-recommends git python3-venv python3-dev libportaudio2 espeak-ng curl

python3 - <<'EOF' || fail "Python 3.11 or newer is required (Raspberry Pi OS Bookworm or later)."
import sys
sys.exit(sys.version_info < (3, 11))
EOF

step "Creating the $SERVICE_USER user"
if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  useradd --system --no-create-home --home-dir "$INSTALL_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"
fi
usermod -aG audio,dialout "$SERVICE_USER"
# Header pins for PTT/COS (/dev/gpiochip*); Raspberry Pi OS has this group.
if getent group gpio >/dev/null; then
  usermod -aG gpio "$SERVICE_USER"
fi
# Settings, recordings, logs and backups: the only part the service can write.
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" "$INSTALL_DIR/data"
chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR/data"
rm -rf "$INSTALL_DIR/.cache"  # pip's cache from when it ran as the service user

step "Installing Python dependencies (this can take a few minutes on a Pi)"
[ -x "$INSTALL_DIR/.venv/bin/python" ] || python3 -m venv "$INSTALL_DIR/.venv"
"$INSTALL_DIR/.venv/bin/pip" install -q --no-cache-dir --upgrade pip
"$INSTALL_DIR/.venv/bin/pip" install -q --no-cache-dir -e "$INSTALL_DIR"
# The service can't write bytecode next to root's files, so compile it now.
"$INSTALL_DIR/.venv/bin/python" -m compileall -q "$INSTALL_DIR/src" >/dev/null

step "Allowing access to CM108 USB radio interfaces"
install -m 644 "$INSTALL_DIR/packaging/99-cm108.rules" /etc/udev/rules.d/99-cm108.rules
udevadm control --reload-rules
udevadm trigger --subsystem-match=hidraw

GENERATED_PASSWORD=""
if [ ! -f "$ENV_FILE" ]; then
  step "Writing $ENV_FILE"
  mkdir -p "$ENV_DIR"
  install -m 640 -o root -g "$SERVICE_USER" "$INSTALL_DIR/packaging/moreopenrepeater.env.example" "$ENV_FILE"
  GENERATED_PASSWORD="$(python3 -c 'import secrets; print(secrets.token_urlsafe(12))')"
  set_env MOREOPENREPEATER_AUTH_USER admin
  set_env MOREOPENREPEATER_AUTH_PASSWORD "$GENERATED_PASSWORD"
  if [ "$LISTEN_LAN" -eq 1 ]; then
    set_env MOREOPENREPEATER_HOST 0.0.0.0
  fi
elif [ "$LISTEN_LAN" -eq 1 ]; then
  echo "Note: --lan only applies to a first install; edit MOREOPENREPEATER_HOST in $ENV_FILE instead."
fi
set_env MOREOPENREPEATER_UPDATE_CHANNEL "$CHANNEL"

if [ "$ALLSTAR" -eq 1 ]; then
  install_allstar
fi

step "Installing and (re)starting the service"
for unit in moreopenrepeater.service moreopenrepeater-update.service moreopenrepeater-update.path; do
  install -m 644 "$INSTALL_DIR/packaging/$unit" "/etc/systemd/system/$unit"
done
systemctl daemon-reload
systemctl enable -q moreopenrepeater
systemctl enable -q --now moreopenrepeater-update.path
systemctl restart moreopenrepeater

PORT="$(env_value MOREOPENREPEATER_PORT)"
PORT="${PORT:-8000}"
printf 'Waiting for the dashboard to come up'
up=0
for _ in $(seq 1 90); do
  # Any HTTP answer (even 401) means it's up.
  if curl -s -o /dev/null "http://127.0.0.1:$PORT/api/session"; then
    up=1
    echo " ok"
    break
  fi
  printf '.'
  sleep 1
done
[ "$up" -eq 1 ] || fail "the dashboard didn't come up; see: journalctl -u moreopenrepeater -n 50"
sleep 5  # and stays up
systemctl is-active -q moreopenrepeater || fail "the service stopped after starting; see: journalctl -u moreopenrepeater -n 50"

HOST="$(env_value MOREOPENREPEATER_HOST)"
echo
if [ -n "$GENERATED_PASSWORD" ]; then
  echo "moreopenrepeater is installed and running ($CHANNEL channel, $(git -C "$INSTALL_DIR" rev-parse --short HEAD))."
else
  echo "moreopenrepeater is updated and running ($CHANNEL channel, $(git -C "$INSTALL_DIR" rev-parse --short HEAD))."
fi
if [ "$HOST" = "127.0.0.1" ] || [ -z "$HOST" ]; then
  echo "  Dashboard (on the Pi):  http://127.0.0.1:$PORT"
  echo "  From other devices:     see \"Security\" in $INSTALL_DIR/docs/raspberry-pi.md"
  echo "                          (Tailscale: sudo tailscale serve --bg $PORT)"
else
  echo "  Dashboard:              http://$(hostname -I | awk '{print $1}'):$PORT"
fi
if [ -n "$GENERATED_PASSWORD" ]; then
  echo "  Sign in as:             admin / $GENERATED_PASSWORD"
  echo "                          (change it in $ENV_FILE, then: sudo systemctl restart moreopenrepeater)"
fi
echo "  Logs:                   journalctl -u moreopenrepeater -f"
if [ "$ALLSTAR" -eq 1 ]; then
  echo
  echo "AllStarLink is installed. If this node is new, run: sudo asl-menu"
  echo "(node number, callsign and password), then choose the node on the"
  echo "dashboard's AllStarLink page."
fi
