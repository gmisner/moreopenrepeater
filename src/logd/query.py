"""Search the collector from a terminal: `docker compose exec logd python -m logd.query ...` (#221, #229).

    python -m logd.query --min-severity error --since 2026-11-18T00:00:00Z
    python -m logd.query -q "audio glitch" --module core --limit 20
    python -m logd.query --event "audio.*" --json
    python -m logd.query --gaps          # what is known to be missing from the record
    python -m logd.query --stats         # what it holds, and what it has dropped

Standard library only, like the health check: it runs in the container, where nothing else is installed.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import urllib.parse
from pathlib import Path
from typing import Any, Optional, Sequence

from .healthcheck import DEFAULT_SOCKET, SOCKET_VARIABLE

TIMEOUT_SECONDS = 15.0
MAX_BYTES = 64 * 1024 * 1024
_NAMES = {10: "DEBUG", 20: "INFO", 30: "WARN", 40: "ERROR", 50: "CRIT"}


class QueryError(Exception):
    pass


def _clock(iso: str) -> str:
    """`2026-11-18T12:00:00.123Z`: to the millisecond, with or without fractional seconds, always UTC."""
    return iso.split("+")[0][:23] + "Z"


def fetch(path: Path, target: str, timeout: float = TIMEOUT_SECONDS) -> Any:
    """GET `target` from the collector's query socket and return the decoded JSON."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(str(path))
            sock.sendall(f"GET {target} HTTP/1.1\r\nHost: logd\r\nConnection: close\r\n\r\n".encode())
            data = b""
            while len(data) < MAX_BYTES:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                data += chunk
    except OSError as error:
        raise QueryError(f"cannot reach the collector at {path}: {error}") from None
    head, _, body = data.partition(b"\r\n\r\n")
    try:
        status = int(head.split(b" ", 2)[1])
        decoded = json.loads(body)
    except (IndexError, ValueError):
        raise QueryError("the collector's answer was not understood") from None
    if status != 200:
        raise QueryError(decoded.get("error", f"the collector answered {status}") if isinstance(decoded, dict) else str(status))
    return decoded


def line(event: dict) -> str:
    context = " ".join(f"{k}={v}" for k, v in event["context"].items())
    text = event["message"] or ""
    return f'{_clock(event["time"])} {_NAMES.get(event["severity"], event["severity"]):5} {event["module"]:5} {event["event"]} {text} {context}'.rstrip()


def build_target(args: argparse.Namespace) -> str:
    params = {
        "q": args.q, "min_severity": args.min_severity, "module": args.module, "event": args.event,
        "correlation_id": args.correlation, "since": args.since, "until": args.until, "limit": args.limit,
    }
    return "/events?" + urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})


def main(argv: Optional[Sequence[str]] = None, env: Optional[dict] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m logd.query", description=__doc__.split("\n\n")[0])
    parser.add_argument("-q", help="words that must all appear in the message, the event name or the context")
    parser.add_argument("--min-severity", help="debug, info, warning, error, critical, or a number")
    parser.add_argument("--module")
    parser.add_argument("--event", help="an event name, or a prefix ending in .*")
    parser.add_argument("--correlation", help="one correlation id: everything that belonged to one thing")
    parser.add_argument("--since", help="seconds since 1970, or ISO 8601")
    parser.add_argument("--until")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--json", action="store_true", help="the answer as it came, not as lines")
    parser.add_argument("--gaps", action="store_true", help="what is known to be missing from the record")
    parser.add_argument("--stats", action="store_true", help="what the collector holds and what it has dropped")
    args = parser.parse_args(argv)
    environment = os.environ if env is None else env
    path = Path(environment.get(SOCKET_VARIABLE) or DEFAULT_SOCKET)
    try:
        if args.stats:
            answer: Any = fetch(path, "/stats")
            print(json.dumps(answer, indent=2))
            return 0
        if args.gaps:
            answer = fetch(path, "/gaps")
            if args.json:
                print(json.dumps(answer, indent=2))
            else:
                for gap in answer["gaps"]:
                    print(f'{_clock(gap["time"])} {gap["kind"]:16} {gap["module"]:5} {gap["count"]} {gap["detail"] or ""}'.rstrip())
            return 0
        answer = fetch(path, build_target(args))
    except QueryError as error:
        print(f"logd.query: {error}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(answer, indent=2))
    else:
        for event in reversed(answer["events"]):  # oldest first, so it reads as it happened
            print(line(event))
        if answer["next_before"] is not None:
            print(f'... more before id {answer["next_before"]}; narrow the search or raise --limit', file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
