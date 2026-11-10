#!/usr/bin/env bash
# Applies a DMR change the dashboard asked for (restart/stop bridges, or
# install DVSwitch). Run as root by moreopenrepeater-dmr-restart.service
# (path unit). See docs/dmr.md.
set -uo pipefail

INSTALL_DIR=/opt/moreopenrepeater
REQUEST="$INSTALL_DIR/data/dmr-restart-request"
STATE_DIR="${STATE_DIRECTORY:-/var/lib/moreopenrepeater-dmr}"
STATUS="$STATE_DIR/status.json"
LOG="$STATE_DIR/dmr.log"
UNITS=(analog_bridge mmdvm_bridge md380-emu)

write_status() {
  python3 - "$STATUS" "$@" <<'EOF'
import json, os, sys, time
path, state, action, started, message = sys.argv[1:6]
status = {
    "state": state,
    "action": action or None,
    "started_at": float(started),
    "message": message or None,
}
if state not in ("running",):
    status["finished_at"] = time.time()
os.makedirs(os.path.dirname(path), exist_ok=True)
with open(path + ".tmp", "w") as f:
    json.dump(status, f)
os.chmod(path + ".tmp", 0o644)
os.replace(path + ".tmp", path)
EOF
}

log() { mkdir -p "$STATE_DIR"; printf '%s\n' "$*" >>"$LOG"; }

if [ ! -f "$REQUEST" ] || [ -L "$REQUEST" ]; then
  rm -f "$REQUEST"
  exit 0
fi
action="$(tr -d '[:space:]' <"$REQUEST" | head -n 1)"
rm -f "$REQUEST"
started="$(date +%s)"

case "$action" in
  stop)
    systemctl stop "${UNITS[@]}" 2>/dev/null || true
    systemctl disable "${UNITS[@]}" 2>/dev/null || true
    ;;
  install)
    write_status running install "$started" "Installing DVSwitch packages…"
    log "install started"
    if ! bash "$INSTALL_DIR/scripts/dmr-install.sh" >>"$LOG" 2>&1; then
      write_status failed install "$started" "DMR install failed; see /var/lib/moreopenrepeater-dmr/dmr.log"
      log "install failed"
      exit 1
    fi
    write_status succeeded install "$started" "DMR is installed. Restarting the dashboard to pick it up."
    log "install succeeded; restarting moreopenrepeater"
    # Env MOREOPENREPEATER_DMR_* is only read at process start.
    systemctl restart moreopenrepeater || true
    ;;
  restart|*)
    systemctl enable "${UNITS[@]}" 2>/dev/null || true
    systemctl restart "${UNITS[@]}" 2>/dev/null || true
    ;;
esac
