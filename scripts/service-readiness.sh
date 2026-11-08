#!/usr/bin/env bash
# Shared by the installer and updater. A listening port alone doesn't tell
# us whether the release can serve its API or stay up.

wait_for_dashboard() {
  local pid
  pid="$(systemctl show -p MainPID --value moreopenrepeater)" || return 1
  [ "$pid" -gt 0 ] || return 1

  # The unit has completed its Type=notify startup. Use the server's effective
  # host and port, including systemd's parsing of quoted environment values.
  python3 - "$pid" <<'PY'
import http.client
import json
import sys
import time
from pathlib import Path

pid = sys.argv[1]
env = dict(entry.split(b"=", 1) for entry in Path(f"/proc/{pid}/environ").read_bytes().split(b"\0") if b"=" in entry)
host = env.get(b"MOREOPENREPEATER_HOST", b"0.0.0.0").decode()
host = {"0.0.0.0": "127.0.0.1", "::": "::1", "": "127.0.0.1"}.get(host, host)
port = int(env.get(b"MOREOPENREPEATER_PORT", b"8000"))

def ready():
    conn = http.client.HTTPConnection(host, port, timeout=2)
    try:
        conn.request("GET", "/api/session")
        response = conn.getresponse()
        if response.status != 200:
            return False
        body = json.loads(response.read(4096))
        return (
            isinstance(body, dict)
            and type(body.get("auth_required")) is bool
            and type(body.get("authenticated")) is bool
            and {"username", "role"} <= body.keys()
        )
    except (OSError, http.client.HTTPException, ValueError):
        return False
    finally:
        conn.close()

for _ in range(10):
    if ready():
        time.sleep(5)
        sys.exit(0 if ready() else 1)
    time.sleep(1)
sys.exit(1)
PY
  local result=$?
  [ "$result" -eq 0 ] &&
    systemctl is-active -q moreopenrepeater &&
    [ "$(systemctl show -p MainPID --value moreopenrepeater)" = "$pid" ]
}
