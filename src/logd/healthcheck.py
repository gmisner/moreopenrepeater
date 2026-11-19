"""Ask the running collector how it is, for a container health check.

`python -m logd.healthcheck` exits 0 when the collector answers and says it is `ok` or `degraded` (running, but
losing events right now), and 1 when it cannot be reached. Standard library only: Docker runs it on a timer and
on a Pi importing anything heavy takes seconds of CPU each time, so a test keeps it that way.
"""
from __future__ import annotations

import json
import os
import socket
import sys
from pathlib import Path
from typing import Mapping, Optional

SOCKET_VARIABLE = "MOREOPENREPEATER_LOGD_QUERY_SOCKET"
DEFAULT_SOCKET = "/run/logd/query.sock"
TIMEOUT_SECONDS = 3.0
MAX_BYTES = 16 * 1024


def check(path: Path, timeout: float = TIMEOUT_SECONDS) -> tuple[bool, str]:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(str(path))
            sock.sendall(b"GET /health HTTP/1.1\r\nHost: logd\r\nConnection: close\r\n\r\n")
            data = b""
            while len(data) < MAX_BYTES:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                data += chunk
    except OSError as error:
        return False, f"cannot reach the collector at {path}: {error}"
    head, _, body = data.partition(b"\r\n\r\n")
    try:
        status = int(head.split(b" ", 2)[1])
        state = json.loads(body)["state"]
    except (IndexError, ValueError, KeyError, TypeError):
        return False, "the collector's answer was not understood"
    if status != 200:
        return False, f"the collector answered {status}"
    return True, f"collector is {state}"


def main(env: Optional[Mapping[str, str]] = None) -> int:
    environment = os.environ if env is None else env
    healthy, reason = check(Path(environment.get(SOCKET_VARIABLE) or DEFAULT_SOCKET))
    print(reason, file=sys.stdout if healthy else sys.stderr)
    return 0 if healthy else 1


if __name__ == "__main__":
    raise SystemExit(main())
