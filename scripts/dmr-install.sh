#!/usr/bin/env bash
# Installs DVSwitch packages and seeds /etc/moreopenrepeater/dmr for the
# dashboard DMR page. Run as root. Used by install-pi.sh --dmr and by the
# dashboard via moreopenrepeater-dmr-restart.service (action=install).
#
# Does not rebuild the app venv; the helper restarts moreopenrepeater so it
# picks up MOREOPENREPEATER_DMR_* from the env file.
set -euo pipefail
umask 022

INSTALL_DIR="${INSTALL_DIR:-/opt/moreopenrepeater}"
SERVICE_USER="${SERVICE_USER:-moreopenrepeater}"
ENV_DIR=/etc/moreopenrepeater
ENV_FILE="$ENV_DIR/env"
DMR_DIR=/etc/moreopenrepeater/dmr

step() { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || fail "run this with sudo."
[ -f "$ENV_FILE" ] || fail "$ENV_FILE is missing; install moreopenrepeater first (scripts/install-pi.sh)."
command -v apt-get >/dev/null || fail "this installer needs a Debian-based system (Raspberry Pi OS, Debian, Ubuntu)."

set_env() {
  local key="$1" value="$2"
  if grep -qE "^#?${key}=" "$ENV_FILE"; then
    sed -i -E "s|^#?${key}=.*|${key}=${value}|" "$ENV_FILE"
  else
    printf '%s=%s\n' "$key" "$value" >>"$ENV_FILE"
  fi
}

dvswitch_repo_script() {
  local codename
  codename="$(sed -nE 's/^VERSION_CODENAME="?([^"]*)"?$/\1/p' /etc/os-release)"
  case "$codename" in
    bookworm|trixie) echo "bookworm" ;;
    *) fail "DVSwitch packages need Debian 12 (bookworm) or 13 (trixie), not '${codename:-this system}'." ;;
  esac
}

step "Installing DVSwitch (Analog_Bridge + MMDVM_Bridge)"
export DEBIAN_FRONTEND=noninteractive
repo_script="$(dvswitch_repo_script)"
if ! dpkg -s analog-bridge >/dev/null 2>&1 || ! dpkg -s mmdvm-bridge >/dev/null 2>&1; then
  curl -fsSL -o "/tmp/dvswitch-$repo_script" "http://dvswitch.org/$repo_script"
  chmod +x "/tmp/dvswitch-$repo_script"
  "/tmp/dvswitch-$repo_script"
  rm -f "/tmp/dvswitch-$repo_script"
  apt-get update -q
  apt-get install -y -q analog-bridge mmdvm-bridge md380-emu || \
    apt-get install -y -q analog-bridge mmdvm-bridge
fi

step "Seeding moreopenrepeater DMR config in $DMR_DIR"
install -d -m 750 -o root -g "$SERVICE_USER" "$DMR_DIR"
for name in Analog_Bridge.ini MMDVM_Bridge.ini DVSwitch.ini; do
  if [ ! -f "$DMR_DIR/$name" ]; then
    install -m 640 -o root -g "$SERVICE_USER" "$INSTALL_DIR/packaging/dmr/$name" "$DMR_DIR/$name"
  fi
done
if [ -f /opt/Analog_Bridge/dvsm.macro ] && [ ! -e "$DMR_DIR/dvsm.macro" ]; then
  ln -s /opt/Analog_Bridge/dvsm.macro "$DMR_DIR/dvsm.macro"
fi
install -d -m 755 /etc/systemd/system/analog_bridge.service.d
install -d -m 755 /etc/systemd/system/mmdvm_bridge.service.d
install -m 644 "$INSTALL_DIR/packaging/dmr/analog_bridge.service.d.conf" \
  /etc/systemd/system/analog_bridge.service.d/60-moreopenrepeater.conf
install -m 644 "$INSTALL_DIR/packaging/dmr/mmdvm_bridge.service.d.conf" \
  /etc/systemd/system/mmdvm_bridge.service.d/60-moreopenrepeater.conf
# Leave bridges disabled until the dashboard saves identity (docs/dmr.md).
systemctl disable --now analog_bridge mmdvm_bridge md380-emu 2>/dev/null || true
systemctl daemon-reload

set_env MOREOPENREPEATER_DMR_DIR "$DMR_DIR"
set_env MOREOPENREPEATER_DMR_USRP_LISTEN "127.0.0.1:34011"
set_env MOREOPENREPEATER_DMR_USRP_PEER "127.0.0.1:34012"
set_env MOREOPENREPEATER_DMR_RESTART_REQUEST "$INSTALL_DIR/data/dmr-restart-request"

printf 'DMR packages and config are ready in %s\n' "$DMR_DIR"
