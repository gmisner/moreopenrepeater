#!/usr/bin/env bash
# Installs (or updates) moreopenrepeater on a Raspberry Pi / Debian system:
#
#   curl -fsSL https://raw.githubusercontent.com/gmisner/moreopenrepeater/main/scripts/install-pi.sh | sudo bash
#
# Safe to run again: it updates the checkout and dependencies and restarts
# the service, but preserves local settings in /etc/moreopenrepeater/env. What it does
# is the manual procedure in docs/raspberry-pi.md. The dashboard's Updates
# page runs it too (through scripts/update.sh).
#
# Options (append after `sudo bash -s --` when piping):
#   --channel stable|beta|dev
#              which releases to follow (default: the one chosen before, or
#              stable); see "Updates" in docs/raspberry-pi.md
#   --localhost
#              listen on 127.0.0.1 only (dashboard only from the Pi itself, or
#              via SSH tunnel / Tailscale Serve). Default is 0.0.0.0 (LAN/Wi-Fi).
#   --lan      explicitly listen on 0.0.0.0 (the default for a new install)
#   --allstar  also install AllStarLink (ASL3) for linking and autopatch, and
#              connect the controller to it (Debian 12 or 13; docs/allstar.md)
#   --dmr      also install DVSwitch (Analog_Bridge + MMDVM_Bridge) for a
#              cross-mode DMR bridge (Debian 12 or 13; docs/dmr.md)
#   --protect-sd / --no-protect-sd
#              keep the system journal and the controller's log in memory to
#              spare the SD card, or stop doing so; remembered for later runs
#              (see "SD card" in docs/raspberry-pi.md)
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
LISTEN_HOST=""
ALLSTAR=0
DMR=0
CHANNEL=""
FETCHED=0
PROTECT_SD=""
ASTERISK_DIR=/etc/asterisk
AMI_USER=moreopenrepeater
JOURNALD_DROPIN=/etc/systemd/journald.conf.d/60-moreopenrepeater.conf
WATCHDOG_DROPIN=/etc/systemd/system.conf.d/60-moreopenrepeater-watchdog.conf
RAM_LOG=/run/moreopenrepeater/moreopenrepeater.log  # the service's RuntimeDirectory

step() { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --localhost) LISTEN_HOST=127.0.0.1 ;;
    --lan) LISTEN_HOST=0.0.0.0 ;;
    --allstar) ALLSTAR=1 ;;
    --dmr) DMR=1 ;;
    --channel) CHANNEL="${2:-}"; shift ;;
    --channel=*) CHANNEL="${1#--channel=}" ;;
    --fetched) FETCHED=1 ;;
    --protect-sd) PROTECT_SD=1 ;;
    --no-protect-sd) PROTECT_SD=0 ;;
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

# True when int8 model + voices are already on disk (sizes from kokoro-cli 0.4).
# Size-only so updates don't re-hash ~120 MB or hit the network again.
kokoro_models_ready() {
  local home="$1"
  local model="$home/models/kokoro-v1.0.int8.onnx"
  local voices="$home/models/voices-v1.0.bin"
  [ -f "$model" ] && [ "$(stat -c%s "$model")" -eq 92361271 ] &&
    [ -f "$voices" ] && [ "$(stat -c%s "$voices")" -eq 28214398 ]
}

# Piper voice: onnx + json config from `python -m piper.download_voices`.
piper_voice_ready() {
  local home="$1"
  local voice="${2:-en_US-amy-low}"
  [ -f "$home/${voice}.onnx" ] && [ -f "$home/${voice}.onnx.json" ]
}

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
  local fresh=0
  if ! dpkg -s asl3 >/dev/null 2>&1; then fresh=1; fi
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

  # Ensure conf files are group asterisk AND group-writable.
  # This matters even after ASL package upgrades that may reset modes.
  local file
  for file in rpt.conf modules.conf echolink.conf rpt_http_registrations.conf \
      extensions.conf simpleusb.conf usbradio.conf voter.conf savenode.conf iax.conf; do
    if [ -f "$ASTERISK_DIR/$file" ]; then
      chgrp asterisk "$ASTERISK_DIR/$file"
      chmod g+w "$ASTERISK_DIR/$file"
    fi
  done

  # AMI logins and the autopatch SIP password must not be world-readable;
  # a fresh ASL3 must not load chan_simpleusb onto the controller's CM108.
  # (scripts/asl-harden.sh — covered by tests/scripts/test_asl_harden.py)
  bash "$INSTALL_DIR/scripts/asl-harden.sh" "$ASTERISK_DIR" "$fresh"
  if [ "$fresh" -eq 1 ]; then
    systemctl restart asterisk
  fi

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

# DVSwitch apt repo installer for this Debian release (docs/dmr.md).
dvswitch_repo_script() {
  local codename
  codename="$(sed -nE 's/^VERSION_CODENAME="?([^"]*)"?$/\1/p' /etc/os-release)"
  case "$codename" in
    bookworm|trixie) echo "bookworm" ;;
    *) fail "DVSwitch packages via install-pi.sh --dmr need Debian 12 (bookworm) or 13 (trixie), not '${codename:-this system}'." ;;
  esac
}

install_dmr() {
  # Shared with the dashboard Install button (scripts/dmr-install.sh).
  bash "$INSTALL_DIR/scripts/dmr-install.sh"
}

# Logs are what a Pi writes most often; in memory they cost the SD card
# nothing, at the price of starting afresh at every reboot.
protect_sd() {
  step "Protecting the SD card: logs in memory"
  mkdir -p "$(dirname "$JOURNALD_DROPIN")"
  cat >"$JOURNALD_DROPIN.new" <<'EOF'
# From moreopenrepeater's install-pi.sh --protect-sd (undo with --no-protect-sd):
# the system journal stays in memory, sparing the SD card, and starts afresh
# at every reboot.
[Journal]
Storage=volatile
RuntimeMaxUse=32M
EOF
  if cmp -s "$JOURNALD_DROPIN.new" "$JOURNALD_DROPIN"; then
    rm -f "$JOURNALD_DROPIN.new"
  else
    mv "$JOURNALD_DROPIN.new" "$JOURNALD_DROPIN"
    chmod 644 "$JOURNALD_DROPIN"
    systemctl restart systemd-journald
  fi
  local log_path
  log_path="$(env_value MOREOPENREPEATER_LOG_PATH)"
  if [ -z "$log_path" ] || [ "$log_path" = "$RAM_LOG" ]; then
    set_env MOREOPENREPEATER_LOG_PATH "$RAM_LOG"
  else
    echo "Note: MOREOPENREPEATER_LOG_PATH is set to $log_path in $ENV_FILE, so the controller's log stays there."
  fi
}

unprotect_sd() {
  if [ -f "$JOURNALD_DROPIN" ]; then
    step "Turning off SD card protection"
    rm -f "$JOURNALD_DROPIN"
    systemctl restart systemd-journald
  fi
  if [ "$(env_value MOREOPENREPEATER_LOG_PATH)" = "$RAM_LOG" ]; then
    sed -i -E "s|^MOREOPENREPEATER_LOG_PATH=.*|#MOREOPENREPEATER_LOG_PATH=|" "$ENV_FILE"
  fi
}

# A Raspberry Pi's own watchdog reboots it if the kernel hangs, as long as
# systemd keeps feeding it. Left alone if something else has set it up.
enable_hardware_watchdog() {
  grep -qas "Raspberry Pi" /proc/device-tree/model || return 0
  [ -e /dev/watchdog0 ] || return 0
  if [ ! -f "$WATCHDOG_DROPIN" ] && [ "$(systemctl show -p RuntimeWatchdogUSec --value)" != 0 ]; then
    return 0
  fi
  mkdir -p "$(dirname "$WATCHDOG_DROPIN")"
  cat >"$WATCHDOG_DROPIN.new" <<'EOF'
# From moreopenrepeater's install-pi.sh: reboot the Pi if the kernel hangs.
[Manager]
RuntimeWatchdogSec=15s
EOF
  if cmp -s "$WATCHDOG_DROPIN.new" "$WATCHDOG_DROPIN"; then
    rm -f "$WATCHDOG_DROPIN.new"
    return 0
  fi
  step "Turning on the Raspberry Pi's hardware watchdog"
  mv "$WATCHDOG_DROPIN.new" "$WATCHDOG_DROPIN"
  chmod 644 "$WATCHDOG_DROPIN"
  systemctl daemon-reexec
}

[ "$(id -u)" -eq 0 ] || fail "run this with sudo."
if [ "$ALLSTAR" -eq 1 ]; then asl_repo_package >/dev/null; fi
if [ "$DMR" -eq 1 ]; then dvswitch_repo_script >/dev/null; fi
command -v apt-get >/dev/null || fail "this installer needs a Debian-based system (Raspberry Pi OS, Debian, Ubuntu)."

if [ -z "$CHANNEL" ] && [ -f "$ENV_FILE" ]; then CHANNEL="$(env_value MOREOPENREPEATER_UPDATE_CHANNEL)"; fi
CHANNEL="${CHANNEL:-stable}"
case "$CHANNEL" in
  stable) CHANNEL_BRANCH=stable ;;
  beta) CHANNEL_BRANCH=beta ;;
  dev) CHANNEL_BRANCH=main ;;
  *) fail "unknown channel '$CHANNEL' (choose stable, beta or dev)." ;;
esac

if [ "${MOREOPENREPEATER_INSTALL_LOCKED:-0}" = 0 ]; then
  exec 9>/run/lock/moreopenrepeater-install.lock
  flock -n 9 || fail "another install or update is running."
  export MOREOPENREPEATER_INSTALL_LOCKED=1
fi

if [ "$FETCHED" -eq 0 ]; then
  BRANCH="${BRANCH:-$CHANNEL_BRANCH}"
  if ! command -v git >/dev/null; then
    step "Installing git"
    apt-get update -q
    apt-get install -y -q --no-install-recommends git
  fi

  commit=""
  step "Fetching moreopenrepeater ($CHANNEL: $BRANCH) into $INSTALL_DIR"
  if [ -d "$INSTALL_DIR/.git" ]; then
    # The code belongs to root (earlier versions gave it to the service
    # user), so the dashboard can't change what the updater runs as root.
    find "$INSTALL_DIR" -path "$INSTALL_DIR/data" -prune -o \( ! -user root -o ! -group root \) -exec chown -h root:root {} +
    git -C "$INSTALL_DIR" fetch -q origin "$BRANCH"
    commit="$(git -C "$INSTALL_DIR" rev-parse FETCH_HEAD)"
  else
    if [ -e "$INSTALL_DIR" ] && [ -n "$(ls -A "$INSTALL_DIR")" ]; then
      fail "$INSTALL_DIR exists and isn't a moreopenrepeater checkout; move it aside first."
    fi
    git clone -q --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
  fi
  # Default ACLs (some systems put them on /opt) would make files writable
  # by everyone whatever the umask. Clear one path at a time so a transient
  # git lock that vanishes mid-run (maintenance.lock) doesn't fail the install.
  if command -v setfacl >/dev/null; then
    find "$INSTALL_DIR" -path "$INSTALL_DIR/data" -prune -o ! -type l -print0 |
      while IFS= read -r -d '' path; do
        setfacl -bk "$path" 2>/dev/null || true
      done
  fi
  find "$INSTALL_DIR" -path "$INSTALL_DIR/data" -prune -o ! -type l -perm /022 -exec chmod go-w {} +

  pass=(--fetched --channel "$CHANNEL")
  case "$LISTEN_HOST" in
    127.0.0.1) pass+=(--localhost) ;;
    0.0.0.0) pass+=(--lan) ;;
  esac
  if [ "$ALLSTAR" -eq 1 ]; then pass+=(--allstar); fi
  if [ "$DMR" -eq 1 ]; then pass+=(--dmr); fi
  if [ "$PROTECT_SD" = 1 ]; then pass+=(--protect-sd); fi
  if [ "$PROTECT_SD" = 0 ]; then pass+=(--no-protect-sd); fi
  if [ -n "$commit" ]; then
    # Build the dependencies from the candidate without changing the live checkout.
    candidate="$(mktemp -d)"
    trap 'rm -rf -- "$candidate"' EXIT
    git -C "$INSTALL_DIR" archive "$commit" | tar -x -C "$candidate"
    installer="$candidate/scripts/install-pi.sh"
    if [ ! -f "$candidate/scripts/service-readiness.sh" ]; then
      # Older releases don't understand the staged handoff. Use this
      # installer with their source and unit files.
      if [ -f "$INSTALL_DIR/scripts/service-readiness.sh" ]; then
        installer="$INSTALL_DIR/scripts/install-pi.sh"
        export INSTALL_HELPERS="$INSTALL_DIR/scripts"
      else
        # Both releases predate staged installs. Keep the legacy handoff
        # and explicitly forward an existing loopback restriction.
        if [ -z "$LISTEN_HOST" ] && [ -f "$ENV_FILE" ] &&
            [ "$(env_value MOREOPENREPEATER_HOST)" = 127.0.0.1 ]; then
          pass+=(--localhost)
        fi
        git -C "$INSTALL_DIR" checkout -q -f -B "$BRANCH" "$commit"
        bash "$INSTALL_DIR/scripts/install-pi.sh" "${pass[@]}"
        exit
      fi
    fi
    INSTALL_CANDIDATE="$candidate" INSTALL_COMMIT="$commit" INSTALL_BRANCH="$BRANCH" \
      bash "$installer" "${pass[@]}"
  else
    bash "$INSTALL_DIR/scripts/install-pi.sh" "${pass[@]}"
  fi
  exit
fi

candidate="${INSTALL_CANDIDATE:-$INSTALL_DIR}"
commit="${INSTALL_COMMIT:-$(git -C "$INSTALL_DIR" rev-parse HEAD)}"
# shellcheck source=scripts/service-readiness.sh
source "${INSTALL_HELPERS:-$candidate/scripts}/service-readiness.sh"

step "Installing system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
# espeak-ng stays as a thin TTS fallback if Kokoro setup fails offline.
apt-get install -y -q --no-install-recommends git python3-venv python3-dev libportaudio2 espeak-ng curl alsa-utils

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
# So the Logs page support bundle can include journalctl -u moreopenrepeater.
if getent group systemd-journal >/dev/null; then
  usermod -aG systemd-journal "$SERVICE_USER"
fi
# Settings, recordings, logs, backups, and Kokoro models: the only part the service can write.
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" "$INSTALL_DIR/data"
chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR/data"
rm -rf "$INSTALL_DIR/.cache"  # pip's cache from when it ran as the service user

step "Installing Python dependencies (this can take a few minutes on a Pi)"
venv_root="$INSTALL_DIR/.venvs"
install -d -m 755 "$venv_root"
# Survive across atomic venv swaps so updates don't re-download onnxruntime / piper voices.
PIP_CACHE_DIR="${PIP_CACHE_DIR:-/var/cache/moreopenrepeater/pip}"
install -d -m 755 "$PIP_CACHE_DIR"
previous_venv="$(readlink "$INSTALL_DIR/.venv-current" || true)"
venv="$(mktemp -d "$venv_root/$commit.XXXXXX")"
# Keep a published environment until the updater has finished using it.
trap 'if [ "$(readlink "$INSTALL_DIR/.venv-current" || true)" != "$venv" ]; then rm -rf -- "$venv"; fi' EXIT
chmod 755 "$venv"
python3 -m venv "$venv"
"$venv/bin/python" -m pip install -q --cache-dir "$PIP_CACHE_DIR" --upgrade pip setuptools wheel
# Piper is the default neural TTS on Pi (lighter than Kokoro). Keep kokoro-cli
# only when the previous venv already had it, so existing sites are not forced.
# [http] pulls Flask so we can keep the ONNX voice loaded in piper.http_server.
dependencies=("piper-tts[http]>=1.2")
installed_venv="${previous_venv:-$INSTALL_DIR/.venv}"
if [ -x "$installed_venv/bin/python" ] &&
    "$installed_venv/bin/python" -m pip show kokoro-cli >/dev/null 2>&1; then
  dependencies+=("kokoro-cli>=0.4")
fi
if [ -x "$installed_venv/bin/python" ] &&
    "$installed_venv/bin/python" -m pip show vosk >/dev/null 2>&1; then
  dependencies+=("vosk>=0.3.45")
fi
"$venv/bin/python" -m pip install -q --cache-dir "$PIP_CACHE_DIR" -e "$candidate" "${dependencies[@]}"

step "Ensuring Piper voice models"
PIPER_HOME="$INSTALL_DIR/data/piper"
PIPER_VOICE="${PIPER_VOICE:-en_US-amy-low}"
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" "$PIPER_HOME"
if piper_voice_ready "$PIPER_HOME" "$PIPER_VOICE"; then
  echo "Piper voice $PIPER_VOICE already present under $PIPER_HOME; skipping download."
else
  if ! sudo -u "$SERVICE_USER" "$venv/bin/python" -m piper.download_voices \
      --data-dir "$PIPER_HOME" "$PIPER_VOICE"; then
    echo "Warning: Piper voice download failed; voice IDs will use espeak-ng until setup succeeds."
    echo "         Retry later with: sudo -u $SERVICE_USER $INSTALL_DIR/.venv-current/bin/python -m piper.download_voices --data-dir $PIPER_HOME $PIPER_VOICE"
  fi
fi
chown -R "$SERVICE_USER:$SERVICE_USER" "$PIPER_HOME"

# Optional: keep Kokoro models current when kokoro-cli is still installed.
if [ -x "$venv/bin/kokoro" ]; then
  step "Ensuring Kokoro voice models"
  KOKORO_HOME="$INSTALL_DIR/data/kokoro"
  install -d -o "$SERVICE_USER" -g "$SERVICE_USER" "$KOKORO_HOME"
  if kokoro_models_ready "$KOKORO_HOME"; then
    echo "Kokoro models already present under $KOKORO_HOME; skipping download."
  else
    if ! sudo -u "$SERVICE_USER" env "KOKORO_HOME=$KOKORO_HOME" \
        "$venv/bin/kokoro" setup --model int8; then
      echo "Warning: Kokoro model download failed; Piper/espeak will be used instead."
    fi
  fi
  chown -R "$SERVICE_USER:$SERVICE_USER" "$KOKORO_HOME"
fi

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
fi
# Fresh installs get the template default. Existing bind settings change
# only with an explicit flag.
if [ -n "$LISTEN_HOST" ]; then
  set_env MOREOPENREPEATER_HOST "$LISTEN_HOST"
fi
set_env MOREOPENREPEATER_UPDATE_CHANNEL "$CHANNEL"
if [ -n "$PROTECT_SD" ]; then set_env MOREOPENREPEATER_PROTECT_SD "$PROTECT_SD"; fi
if [ "$(env_value MOREOPENREPEATER_PROTECT_SD)" = 1 ]; then protect_sd; else unprotect_sd; fi

if [ "$candidate" != "$INSTALL_DIR" ]; then
  step "Checking out $commit"
  git -C "$INSTALL_DIR" checkout -q -f -B "$INSTALL_BRANCH" "$commit"
  # Keep imports tied to the canonical checkout for data, web assets, and Git.
  "$venv/bin/python" -m pip install -q --no-deps --no-build-isolation -e "$INSTALL_DIR"
fi
"$venv/bin/python" -m compileall -q "$INSTALL_DIR/src" >/dev/null

if [ "$ALLSTAR" -eq 1 ]; then
  install_allstar
else
  # Even when run without --allstar, re-apply group membership + conf
  # permissions if ASL3 is already present.  Beta updates often omit
  # --allstar, and ASL package upgrades can replace conf modes, leaving
  # the service user without write access.
  if [ -f "$ASTERISK_DIR/rpt.conf" ]; then
    step "Re-applying AllStarLink group permissions (update without --allstar)"
    usermod -aG asterisk "$SERVICE_USER" 2>/dev/null || true
    for file in rpt.conf modules.conf echolink.conf rpt_http_registrations.conf \
        extensions.conf simpleusb.conf usbradio.conf voter.conf savenode.conf iax.conf; do
      if [ -f "$ASTERISK_DIR/$file" ]; then
        chgrp asterisk "$ASTERISK_DIR/$file" 2>/dev/null || true
        chmod g+w "$ASTERISK_DIR/$file"
      fi
    done
  fi
fi
if [ "$DMR" -eq 1 ]; then
  install_dmr
fi

step "Installing and (re)starting the service"
for unit in moreopenrepeater.service moreopenrepeater-update.service moreopenrepeater-update.path \
            moreopenrepeater-tailscale.service moreopenrepeater-tailscale.path \
            moreopenrepeater-restart.service moreopenrepeater-restart.path \
            moreopenrepeater-dmr-restart.service moreopenrepeater-dmr-restart.path; do
  install -m 644 "$INSTALL_DIR/packaging/$unit" "/etc/systemd/system/$unit"
done
# Keep the packaged unit usable for manual installs with the legacy .venv.
service_dropin=/etc/systemd/system/moreopenrepeater.service.d/60-release-venv.conf
install -d -m 755 "$(dirname "$service_dropin")"
cat >"$service_dropin" <<EOF
[Service]
Environment=PATH=$INSTALL_DIR/.venv-current/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin
ExecStart=
ExecStart=$INSTALL_DIR/.venv-current/bin/moreopenrepeater-api
EOF
# Virtualenv console scripts contain absolute paths, so publish a link
# instead of moving the environment. The legacy .venv is left intact.
ln -sfnT "$venv" "$INSTALL_DIR/.venv-current.new"
mv -Tf "$INSTALL_DIR/.venv-current.new" "$INSTALL_DIR/.venv-current"
systemctl daemon-reload
enable_hardware_watchdog
systemctl enable -q moreopenrepeater
systemctl enable -q --now moreopenrepeater-update.path
systemctl enable -q --now moreopenrepeater-tailscale.path
systemctl enable -q --now moreopenrepeater-restart.path
systemctl enable -q --now moreopenrepeater-dmr-restart.path
systemctl restart moreopenrepeater

wait_for_dashboard || fail "the dashboard didn't come up; see: journalctl -u moreopenrepeater -n 50"
# The running environment and its immediate predecessor are both retained.
for old_venv in "$venv_root"/*; do
  [ "$old_venv" = "$venv" ] || [ "$old_venv" = "$previous_venv" ] || rm -rf -- "$old_venv"
done

PORT="$(env_value MOREOPENREPEATER_PORT)"
PORT="${PORT:-8000}"
HOST="$(env_value MOREOPENREPEATER_HOST)"
echo
if [ -n "$GENERATED_PASSWORD" ]; then
  echo "moreopenrepeater is installed and running ($CHANNEL channel, $(git -C "$INSTALL_DIR" rev-parse --short HEAD))."
else
  echo "moreopenrepeater is updated and running ($CHANNEL channel, $(git -C "$INSTALL_DIR" rev-parse --short HEAD))."
fi
if [ "$HOST" = "127.0.0.1" ]; then
  echo "  Dashboard (on the Pi):  http://127.0.0.1:$PORT"
  echo "  From other devices:     SSH tunnel, Tailscale, or set MOREOPENREPEATER_HOST=0.0.0.0"
  echo "                          (see \"Security\" in $INSTALL_DIR/docs/raspberry-pi.md)"
else
  echo "  Dashboard:              http://$(hostname -I | awk '{print $1}'):$PORT"
  echo "                          (also http://127.0.0.1:$PORT on the Pi)"
fi
if [ -n "$GENERATED_PASSWORD" ]; then
  echo "  Sign in as:             admin / $GENERATED_PASSWORD"
  echo "                          (change it in $ENV_FILE, then: sudo systemctl restart moreopenrepeater)"
fi
echo "  Logs:                   journalctl -u moreopenrepeater -f"
if [ "$ALLSTAR" -eq 1 ]; then
  echo
  echo "AllStarLink is installed. If this node is new, enter its portal"
  echo "node number, callsign and password on the dashboard's AllStarLink"
  echo "page (or run: sudo asl-menu)."
fi
if [ "$DMR" -eq 1 ]; then
  echo
  echo "DVSwitch DMR is installed. Enter callsign, DMR Id, network password"
  echo "and talkgroup on the dashboard's DMR page (docs/dmr.md)."
fi
