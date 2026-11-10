#!/usr/bin/env bash
# Restarts or stops DVSwitch DMR bridges when the dashboard asks. Run as root
# by moreopenrepeater-dmr-restart.service (path unit). See docs/dmr.md.
set -uo pipefail

INSTALL_DIR=/opt/moreopenrepeater
REQUEST="$INSTALL_DIR/data/dmr-restart-request"
UNITS=(analog_bridge mmdvm_bridge md380-emu)

if [ ! -f "$REQUEST" ] || [ -L "$REQUEST" ]; then
  rm -f "$REQUEST"
  exit 0
fi
action="$(tr -d '[:space:]' <"$REQUEST" | head -n 1)"
rm -f "$REQUEST"
case "$action" in
  stop)
    systemctl stop "${UNITS[@]}" 2>/dev/null || true
    systemctl disable "${UNITS[@]}" 2>/dev/null || true
    ;;
  restart|*)
    systemctl enable "${UNITS[@]}" 2>/dev/null || true
    systemctl restart "${UNITS[@]}" 2>/dev/null || true
    ;;
esac
