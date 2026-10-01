#!/usr/bin/env bash
# Installs the release channel the dashboard asked for. Run as root by
# moreopenrepeater-update.service, which moreopenrepeater-update.path starts
# when the dashboard writes the request file; not meant to be run by hand
# (use install-pi.sh --channel ... for that).
#
# The request file is in the service's data folder, so its contents are
# untrusted: only an exact channel name is accepted. Progress goes to
# status.json and update.log in the state directory, which the service can
# read but not write. If the new version doesn't come up, the previous commit
# is reinstalled.
set -uo pipefail

INSTALL_DIR=/opt/moreopenrepeater
REQUEST="$INSTALL_DIR/data/update-request"
STATE_DIR="${STATE_DIRECTORY:-/var/lib/moreopenrepeater-update}"
STATUS="$STATE_DIR/status.json"
LOG="$STATE_DIR/update.log"
ENV_FILE=/etc/moreopenrepeater/env

# write_status STATE CHANNEL STARTED FROM TO MESSAGE
write_status() {
  python3 - "$STATUS" "$@" <<'EOF'
import json, os, sys, time
path, state, channel, started, from_sha, to_sha, message = sys.argv[1:]
status = {"state": state, "channel": channel, "started_at": float(started), "from_sha": from_sha or None,
          "to_sha": to_sha or None, "message": message or None}
if state != "running":
    status["finished_at"] = time.time()
with open(path + ".tmp", "w") as f:
    json.dump(status, f)
os.chmod(path + ".tmp", 0o644)
os.replace(path + ".tmp", path)
EOF
}

head_commit() { git -C "$INSTALL_DIR" rev-parse HEAD 2>/dev/null; }

# Runs the checkout's installer from a copy, since the installer replaces
# its own file when it fetches.
run_installer() {
  local copy="$STATE_DIR/install-pi.sh"
  cp "$INSTALL_DIR/scripts/install-pi.sh" "$copy"
  bash "$copy" "$@" >>"$LOG" 2>&1
}

main() {
  # Ignore anything that isn't a plain file (a symlink to somewhere else, a FIFO).
  if [ ! -f "$REQUEST" ] || [ -L "$REQUEST" ]; then
    rm -f "$REQUEST"
    exit 0
  fi
  local channel
  channel="$(head -c 32 "$REQUEST" | LC_ALL=C tr -dc '[:lower:]')"
  rm -f "$REQUEST"
  local started
  started="$(date +%s.%N)"
  case "$channel" in
    dev | beta | stable) ;;
    *)
      write_status failed "" "$started" "" "" "The request named an unknown channel."
      exit 1
      ;;
  esac

  local previous_commit previous_channel
  previous_commit="$(head_commit)"
  previous_channel="$(sed -nE 's/^MOREOPENREPEATER_UPDATE_CHANNEL=(.*)/\1/p' "$ENV_FILE" | tail -n 1)"
  previous_channel="${previous_channel:-$channel}"

  : >"$LOG"
  chmod 644 "$LOG"
  echo "Updating to the $channel channel (from ${previous_commit:0:7}, $previous_channel) at $(date)" >>"$LOG"
  write_status running "$channel" "$started" "$previous_commit" "" ""

  if run_installer --channel "$channel"; then
    write_status succeeded "$channel" "$started" "$previous_commit" "$(head_commit)" ""
    return
  fi

  local failed_commit
  failed_commit="$(head_commit)"
  if [ -z "$previous_commit" ] || [ "$failed_commit" = "$previous_commit" ]; then
    # Still on the same commit (e.g. GitHub was unreachable): nothing to go back to.
    write_status failed "$channel" "$started" "$previous_commit" "" "The update failed without changing versions; see the log."
    return
  fi
  echo >>"$LOG"
  echo "The update failed; going back to ${previous_commit:0:7}." >>"$LOG"
  if git -C "$INSTALL_DIR" checkout -q -f --detach "$previous_commit" >>"$LOG" 2>&1 &&
    run_installer --fetched --channel "$previous_channel"; then
    write_status rolled_back "$channel" "$started" "$previous_commit" "$failed_commit" \
      "${failed_commit:0:7} didn't start, so the previous version was put back."
  else
    write_status failed "$channel" "$started" "$previous_commit" "$failed_commit" \
      "The update failed and so did going back to the previous version; see the log."
  fi
}

main "$@"
