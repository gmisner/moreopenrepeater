#!/usr/bin/env bash
# Applies a Tailscale change the dashboard asked for. Run as root by
# moreopenrepeater-tailscale.service, which moreopenrepeater-tailscale.path
# starts when the dashboard writes the request file; not meant to be run by
# hand.
#
# The request file is in the service's data folder, so its contents are
# untrusted: only a known action (and, for up, an optional auth key shaped
# like Tailscale's) is accepted. Auth keys are never written to the log.
# Progress goes to status.json and tailscale.log in the state directory,
# which the service can read but not write.
set -uo pipefail

INSTALL_DIR=/opt/moreopenrepeater
REQUEST="$INSTALL_DIR/data/tailscale-request"
STATE_DIR="${STATE_DIRECTORY:-/var/lib/moreopenrepeater-tailscale}"
STATUS="$STATE_DIR/status.json"
LOG="$STATE_DIR/tailscale.log"
ENV_FILE=/etc/moreopenrepeater/env
SERVICE_USER=moreopenrepeater
OPERATOR="$SERVICE_USER"

# write_status STATE ACTION STARTED MESSAGE [AUTH_URL]
write_status() {
  python3 - "$STATUS" "$@" <<'EOF'
import json, os, sys, time
path, state, action, started, message = sys.argv[1:6]
auth_url = sys.argv[6] if len(sys.argv) > 6 else ""
status = {
    "state": state,
    "action": action or None,
    "started_at": float(started),
    "message": message or None,
    "auth_url": auth_url or None,
}
if state not in ("running", "needs_login"):
    status["finished_at"] = time.time()
with open(path + ".tmp", "w") as f:
    json.dump(status, f)
os.chmod(path + ".tmp", 0o644)
os.replace(path + ".tmp", path)
EOF
}

log() { printf '%s\n' "$*" >>"$LOG"; }

env_value() {
  local key="$1"
  [ -f "$ENV_FILE" ] || return 0
  sed -nE "s/^${key}=(.*)/\\1/p" "$ENV_FILE" | tail -n 1
}

dashboard_port() {
  local port
  port="$(env_value MOREOPENREPEATER_PORT)"
  case "$port" in
    "" ) echo 8000 ;;
    *[!0-9]* ) echo 8000 ;;
    *)
      if [ "$port" -ge 1 ] && [ "$port" -le 65535 ]; then
        echo "$port"
      else
        echo 8000
      fi
      ;;
  esac
}

have_tailscale() { command -v tailscale >/dev/null 2>&1; }

# Parse the request into ACTION and AUTH_KEY via a small Python reader that
# only accepts the whitelist. Prints "ACTION" or "ACTION\tAUTH_KEY".
parse_request() {
  python3 - "$REQUEST" <<'EOF'
import json, re, sys
from pathlib import Path

path = Path(sys.argv[1])
if not path.is_file() or path.is_symlink():
    sys.exit(2)
raw = path.read_bytes()
if len(raw) > 4096:
    sys.exit(3)
try:
    data = json.loads(raw.decode("utf-8"))
except (UnicodeDecodeError, ValueError):
    sys.exit(3)
if not isinstance(data, dict):
    sys.exit(3)
action = data.get("action")
if action not in ("install", "up", "serve-on", "serve-off", "down"):
    sys.exit(3)
auth_key = data.get("auth_key") or ""
if not isinstance(auth_key, str):
    sys.exit(3)
auth_key = auth_key.strip()
if auth_key and (len(auth_key) > 200 or not re.fullmatch(r"tskey-[A-Za-z0-9_-]+", auth_key)):
    sys.exit(3)
if action != "up" and auth_key:
    sys.exit(3)
if auth_key:
    print(action + "\t" + auth_key)
else:
    print(action)
EOF
}

install_tailscale() {
  if have_tailscale; then
    log "Tailscale is already installed ($(tailscale version 2>/dev/null | head -n 1))."
    return 0
  fi
  if ! command -v curl >/dev/null 2>&1; then
    log "curl is required to install Tailscale."
    return 1
  fi
  log "Installing Tailscale from pkgs.tailscale.com…"
  # Official install script adds their apt repo and installs the package.
  if ! curl -fsSL https://tailscale.com/install.sh | sh >>"$LOG" 2>&1; then
    log "The Tailscale install script failed."
    return 1
  fi
  have_tailscale || { log "tailscale wasn't on PATH after install."; return 1; }
  log "Installed $(tailscale version 2>/dev/null | head -n 1)."
}

backend_state() {
  tailscale status --json 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin).get("BackendState",""))' 2>/dev/null || true
}

auth_url() {
  tailscale status --json 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin).get("AuthURL") or "")' 2>/dev/null || true
}

join_tailnet() {
  local key="${1:-}"
  local args=(up --operator="$OPERATOR" --timeout=60s)
  if [ -n "$key" ]; then
    args+=(--auth-key="$key")
    log "Joining the tailnet with an auth key (operator=$OPERATOR)…"
  else
    log "Starting Tailscale login (operator=$OPERATOR)…"
  fi
  # Auth keys must not appear in the log; redirect only stdout/stderr of the
  # command, which Tailscale doesn't echo the key into.
  if ! tailscale "${args[@]}" >>"$LOG" 2>&1; then
    # NeedsLogin with an AuthURL is success for the interactive path.
    local state url
    state="$(backend_state)"
    url="$(auth_url)"
    if [ -z "$key" ] && [ "$state" = "NeedsLogin" ] && [ -n "$url" ]; then
      log "Waiting for login in a browser."
      write_status needs_login up "$STARTED" "Open the login link to join this controller to your tailnet." "$url"
      return 10
    fi
    log "tailscale up failed (state=${state:-unknown})."
    return 1
  fi
  # Ensure operator sticks even when an older up already had the node logged in.
  tailscale set --operator="$OPERATOR" >>"$LOG" 2>&1 || true
  state="$(backend_state)"
  url="$(auth_url)"
  if [ "$state" = "NeedsLogin" ] && [ -n "$url" ]; then
    log "Waiting for login in a browser."
    write_status needs_login up "$STARTED" "Open the login link to join this controller to your tailnet." "$url"
    return 10
  fi
  if [ "$state" != "Running" ] && [ "$state" != "Starting" ]; then
    log "Tailscale is not connected (state=${state:-unknown})."
    return 1
  fi
  log "Connected to the tailnet (state=$state)."
}

serve_on() {
  local port
  port="$(dashboard_port)"
  log "Enabling Tailscale Serve for http://127.0.0.1:$port …"
  tailscale serve --bg --yes "$port" >>"$LOG" 2>&1
}

serve_off() {
  log "Turning Tailscale Serve off…"
  # `off` clears Serve; `reset` is also accepted on newer builds.
  if ! tailscale serve off >>"$LOG" 2>&1; then
    tailscale serve reset >>"$LOG" 2>&1
  fi
}

leave_tailnet() {
  log "Leaving the tailnet…"
  tailscale serve off >>"$LOG" 2>&1 || true
  tailscale logout >>"$LOG" 2>&1
}

main() {
  if [ ! -f "$REQUEST" ] || [ -L "$REQUEST" ]; then
    rm -f "$REQUEST"
    exit 0
  fi

  local parsed action auth_key=""
  if ! parsed="$(parse_request)"; then
    rm -f "$REQUEST"
    STARTED="$(date +%s.%N)"
    : >"$LOG"
    chmod 644 "$LOG"
    write_status failed "" "$STARTED" "The request wasn't a known Tailscale action."
    exit 1
  fi
  rm -f "$REQUEST"

  action="${parsed%%$'\t'*}"
  if [ "$parsed" != "$action" ]; then
    auth_key="${parsed#*$'\t'}"
  fi

  STARTED="$(date +%s.%N)"
  : >"$LOG"
  chmod 644 "$LOG"
  log "Tailscale request: $action at $(date)"
  write_status running "$action" "$STARTED" ""

  case "$action" in
    install)
      if install_tailscale; then
        write_status succeeded install "$STARTED" "Tailscale is installed."
        exit 0
      fi
      write_status failed install "$STARTED" "Couldn't install Tailscale; see the log."
      exit 1
      ;;
    up)
      if ! have_tailscale; then
        write_status failed up "$STARTED" "Install Tailscale first."
        exit 1
      fi
      join_tailnet "$auth_key"
      local rc=$?
      if [ "$rc" -eq 10 ]; then
        exit 0
      fi
      if [ "$rc" -eq 0 ]; then
        write_status succeeded up "$STARTED" "Joined the tailnet."
        exit 0
      fi
      write_status failed up "$STARTED" "Couldn't join the tailnet; see the log."
      exit 1
      ;;
    serve-on)
      if ! have_tailscale; then
        write_status failed serve-on "$STARTED" "Install Tailscale first."
        exit 1
      fi
      if serve_on; then
        write_status succeeded serve-on "$STARTED" "Serve is on."
        exit 0
      fi
      write_status failed serve-on "$STARTED" "Couldn't enable Serve; see the log."
      exit 1
      ;;
    serve-off)
      if ! have_tailscale; then
        write_status failed serve-off "$STARTED" "Install Tailscale first."
        exit 1
      fi
      if serve_off; then
        write_status succeeded serve-off "$STARTED" "Serve is off."
        exit 0
      fi
      write_status failed serve-off "$STARTED" "Couldn't turn Serve off; see the log."
      exit 1
      ;;
    down)
      if ! have_tailscale; then
        write_status failed down "$STARTED" "Install Tailscale first."
        exit 1
      fi
      if leave_tailnet; then
        write_status succeeded down "$STARTED" "Left the tailnet."
        exit 0
      fi
      write_status failed down "$STARTED" "Couldn't leave the tailnet; see the log."
      exit 1
      ;;
  esac
}

main "$@"
