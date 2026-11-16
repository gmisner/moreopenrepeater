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

# DVSwitch publishes one apt suite for both Debian 12 and 13.
dvswitch_suite() {
  local codename
  codename="$(sed -nE 's/^VERSION_CODENAME="?([^"]*)"?$/\1/p' /etc/os-release)"
  case "$codename" in
    bookworm|trixie) echo "bookworm" ;;
    *) fail "DVSwitch packages need Debian 12 (bookworm) or 13 (trixie), not '${codename:-this system}'." ;;
  esac
}

# This script runs as root, so it never executes anything it downloaded. The
# DVSwitch apt keyring ships in this repo (packaging/dmr/dvswitch-keyring.gpg)
# and is checked against the pinned fingerprint below; apt then verifies every
# package against that key. To rotate it, replace the file, update the
# fingerprint, and confirm the new key with DVSwitch first.
DVSWITCH_KEY_FPR="6DDFC33CD5FC678659642DF572147EC1E788D4C3"
DVSWITCH_KEYRING=/usr/share/keyrings/dvswitch-keyring.gpg
DVSWITCH_LIST=/etc/apt/sources.list.d/dvswitch.list

install_dvswitch_repo() {
  local suite="$1" shipped="$INSTALL_DIR/packaging/dmr/dvswitch-keyring.gpg" home found
  [ -f "$shipped" ] || fail "missing $shipped; reinstall moreopenrepeater (scripts/install-pi.sh)."
  apt-get install -y -q --no-install-recommends gnupg ca-certificates
  home="$(mktemp -d)"
  # Primary key fingerprint only (the line after "pub:"), compared in full.
  found="$(GNUPGHOME="$home" gpg --show-keys --with-colons --fingerprint "$shipped" 2>/dev/null |
    awk -F: '/^pub:/ {want=1; next} want && /^fpr:/ {print $10; want=0}')"
  rm -rf "$home"
  [ "$found" = "$DVSWITCH_KEY_FPR" ] ||
    fail "the DVSwitch keyring does not match the pinned fingerprint (got '${found:-none}'); not installing."
  install -m 644 -o root -g root "$shipped" "$DVSWITCH_KEYRING"
  printf '# DVSwitch repository, managed by moreopenrepeater (scripts/dmr-install.sh)\ndeb [signed-by=%s] https://dvswitch.org/DVSwitch_Repository %s hamradio\n' \
    "$DVSWITCH_KEYRING" "$suite" >"$DVSWITCH_LIST.new"
  mv "$DVSWITCH_LIST.new" "$DVSWITCH_LIST"
  rm -f /var/lib/apt/lists/dvswitch.org*
}

step "Installing DVSwitch (Analog_Bridge + MMDVM_Bridge)"
export DEBIAN_FRONTEND=noninteractive
suite="$(dvswitch_suite)"
if ! dpkg -s analog-bridge >/dev/null 2>&1 || ! dpkg -s mmdvm-bridge >/dev/null 2>&1; then
  install_dvswitch_repo "$suite"
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
