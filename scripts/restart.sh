#!/usr/bin/env bash
# Restarts the moreopenrepeater service when the dashboard asks. Run as root
# by moreopenrepeater-restart.service, which moreopenrepeater-restart.path
# starts when the dashboard writes the request file; not meant to be run by
# hand (use systemctl restart moreopenrepeater for that).
set -uo pipefail

INSTALL_DIR=/opt/moreopenrepeater
REQUEST="$INSTALL_DIR/data/restart-request"

# Ignore anything that isn't a plain file (a symlink to somewhere else, a FIFO).
if [ ! -f "$REQUEST" ] || [ -L "$REQUEST" ]; then
  rm -f "$REQUEST"
  exit 0
fi
rm -f "$REQUEST"
exec systemctl restart moreopenrepeater
