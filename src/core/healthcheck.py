"""Ask the running core how it is, for a container health check.

`python -m core.healthcheck` exits 0 when the core's own `/health` says it is not
failed, and 1 when it says failed or cannot be reached. It uses the standard
library only and never writes anything.
"""
from __future__ import annotations

import json
import os
import socket
import sys
from pathlib import Path
from typing import Mapping, Optional

from .control import socket_path_from_env

TIMEOUT_SECONDS = 3.0
MAX_BYTES = 64 * 1024


def check(path: Path, timeout: float = TIMEOUT_SECONDS) -> tuple[bool, str]:
    """(healthy, reason). A degraded or starting core is healthy: it is running."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(str(path))
            sock.sendall(b"GET /health HTTP/1.1\r\nHost: core\r\nConnection: close\r\n\r\n")
            data = b""
            while len(data) < MAX_BYTES:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                data += chunk
    except OSError as error:
        return False, f"cannot reach the core at {path}: {error}"
    head, _, body = data.partition(b"\r\n\r\n")
    if b" 200 " not in head.split(b"\r\n", 1)[0]:
        return False, f"unexpected answer: {head[:60]!r}"
    try:
        state = json.loads(body)["state"]
    except (ValueError, KeyError, TypeError):
        return False, "the answer was not a health report"
    return state != "failed", f"core health is {state}"


def main(env: Optional[Mapping[str, str]] = None) -> int:
    path = socket_path_from_env(dict(os.environ if env is None else env))
    if path is None:
        print("MOREOPENREPEATER_CORE_SOCKET is not set", file=sys.stderr)
        return 1
    healthy, reason = check(path)
    print(reason, file=sys.stderr if not healthy else sys.stdout)
    return 0 if healthy else 1


if __name__ == "__main__":
    sys.exit(main())
