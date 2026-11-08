#!/usr/bin/env bash
# Installs the release channel the dashboard asked for. Run as root by
# moreopenrepeater-update.service, which moreopenrepeater-update.path starts
# when the dashboard writes the request file; not meant to be run by hand
# (use install-pi.sh --channel ... for that).
#
# The request file is in the service's data folder, so its contents are
# untrusted: only an exact channel name is accepted. Progress goes to
# status.json and update.log in the state directory, which the service can
# read but not write. If the new version doesn't come up, restart the
# previous checkout with its retained environment.
set -uo pipefail

INSTALL_DIR=/opt/moreopenrepeater
REQUEST="$INSTALL_DIR/data/update-request"
STATE_DIR="${STATE_DIRECTORY:-/var/lib/moreopenrepeater-update}"
STATUS="$STATE_DIR/status.json"
LOG="$STATE_DIR/update.log"
ENV_FILE=/etc/moreopenrepeater/env
UNIT_DIR=/etc/systemd/system
VENV_LINK="$INSTALL_DIR/.venv-current"
UNIT_DROPIN="$UNIT_DIR/moreopenrepeater.service.d/60-release-venv.conf"
# shellcheck source=scripts/service-readiness.sh
source "$INSTALL_DIR/scripts/service-readiness.sh" || exit 1

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
  cp "$INSTALL_DIR/scripts/install-pi.sh" "$copy" || return 1
  bash "$copy" "$@" >>"$LOG" 2>&1
}

main() {
  # Hold the same lock as the manual installer through snapshot and rollback.
  exec 9>/run/lock/moreopenrepeater-install.lock || return 1
  flock 9 || return 1
  export MOREOPENREPEATER_INSTALL_LOCKED=1

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

  local previous_commit
  previous_commit="$(head_commit)"

  : >"$LOG"
  chmod 644 "$LOG"
  echo "Updating to the $channel channel (from ${previous_commit:0:7}) at $(date)" >>"$LOG"
  write_status running "$channel" "$started" "$previous_commit" "" ""

  # These files select the service's environment and configuration, including
  # a legacy install with only .venv and no release drop-in. Use a private
  # directory because the environment file may hold passwords.
  local -a files=("$ENV_FILE" "$VENV_LINK" "$UNIT_DROPIN")
  local unit file
  for unit in moreopenrepeater.service moreopenrepeater-update.service moreopenrepeater-update.path \
      moreopenrepeater-tailscale.service moreopenrepeater-tailscale.path \
      moreopenrepeater-restart.service moreopenrepeater-restart.path; do
    files+=("$UNIT_DIR/$unit")
  done
  local snapshot
  if ! snapshot="$(mktemp -d "$STATE_DIR/rollback.XXXXXX")"; then
    write_status failed "$channel" "$started" "$previous_commit" "" "Could not save the current installation; see the log."
    return 1
  fi
  ROLLBACK_SNAPSHOT="$snapshot"
  trap 'rm -rf -- "$ROLLBACK_SNAPSHOT"' EXIT
  for file in "${files[@]}"; do
    if [ -e "$file" ] || [ -L "$file" ]; then
      if ! cp -a "$file" "$snapshot/${file##*/}"; then
        write_status failed "$channel" "$started" "$previous_commit" "" "Could not save the current installation; see the log."
        return 1
      fi
    fi
  done

  if run_installer --channel "$channel"; then
    write_status succeeded "$channel" "$started" "$previous_commit" "$(head_commit)" ""
    return
  fi

  local failed_commit failed_venv
  failed_commit="$(head_commit)"
  failed_venv="$(readlink "$VENV_LINK" || true)"
  if [ -z "$previous_commit" ]; then
    write_status failed "$channel" "$started" "" "$failed_commit" "The update failed with no previous version to restore; see the log."
    return 1
  fi

  if [ "$failed_commit" = "$previous_commit" ]; then
    # A fetch or dependency failure can leave the live install untouched.
    local unchanged=1
    for file in "${files[@]}"; do
      local saved="$snapshot/${file##*/}"
      if [ -L "$file" ] || [ -L "$saved" ]; then
        [ "$(readlink "$file")" = "$(readlink "$saved")" ] || unchanged=0
      elif [ -e "$file" ] || [ -e "$saved" ]; then
        cmp -s "$file" "$saved" || unchanged=0
      fi
    done
    if [ "$unchanged" -eq 1 ]; then
      write_status failed "$channel" "$started" "$previous_commit" "" "The update failed without changing the installation; see the log."
      return 1
    fi
  fi

  echo >>"$LOG"
  echo "The update failed; going back to ${previous_commit:0:7}." >>"$LOG"
  if restore_installation "$previous_commit" "$snapshot" "${files[@]}" >>"$LOG" 2>&1; then
    if [ "${failed_venv%/*}" = "$INSTALL_DIR/.venvs" ] &&
        [[ "${failed_venv##*/}" == "$failed_commit".?????? ]] &&
        [ "$failed_venv" != "$(readlink "$VENV_LINK" || true)" ]; then
      rm -rf -- "$failed_venv"
    fi
    write_status rolled_back "$channel" "$started" "$previous_commit" "$failed_commit" \
      "${failed_commit:0:7} didn't start, so the previous version was put back."
  else
    write_status failed "$channel" "$started" "$previous_commit" "$failed_commit" \
      "The update failed and so did going back to the previous version; see the log."
    return 1
  fi
}

restore_installation() {
  local commit="$1" snapshot="$2" file saved
  shift 2
  git -C "$INSTALL_DIR" checkout -q -f --detach "$commit" || return 1
  for file in "$@"; do
    saved="$snapshot/${file##*/}"
    if [ -e "$saved" ] || [ -L "$saved" ]; then
      rm -f "$file" && cp -a "$saved" "$file" || return 1
    else
      rm -f "$file" || return 1
    fi
  done
  systemctl daemon-reload && systemctl restart moreopenrepeater && wait_for_dashboard
}

main "$@"
