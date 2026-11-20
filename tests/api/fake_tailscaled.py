"""A stand-in for tailscaled's LocalAPI, listening on a real Unix socket.

It answers like the real one where that matters (the Host header it insists on, chunked JSON, 403 and 404) and records
every request so a test can say what the dashboard asked for and that it only ever read."""
from __future__ import annotations

import socketserver
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path

STATUS = {
    "BackendState": "Running",
    "Self": {"DNSName": "pi.tail123.ts.net.", "HostName": "pi"},
    "AuthURL": "",
}
SERVE = {"TCP": {"443": {"HTTPS": True}}, "Web": {"pi.tail123.ts.net:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8000"}}}}}


class Reply:
    def __init__(self, status=200, body=b"", chunked=False, hang=False):
        self.status, self.body, self.chunked, self.hang = status, body, chunked, hang


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # silence
        pass

    def _answer(self):
        server = self.server
        server.requests.append({"method": self.command, "path": self.path, "headers": dict(self.headers.items())})
        # What tailscaled does: any other Host, or an Origin or Referer, is refused.
        if self.headers.get("Host") != "local-tailscaled.sock" or self.headers.get("Origin") or self.headers.get("Referer"):
            return self._send(Reply(403, b"invalid localapi request"))
        if self.command != "GET":
            return self._send(Reply(403, b"access denied"))  # the dashboard's user is not root or the operator
        route = self.path.split("?")[0]
        self._send(server.routes.get(route, Reply(404, b"404 page not found")))

    do_GET = do_POST = do_PUT = do_DELETE = _answer

    def _send(self, reply):
        if reply.hang:
            time.sleep(30)
            return
        self.send_response(reply.status)
        self.send_header("Content-Type", "application/json")
        if reply.chunked:
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            half = len(reply.body) // 2
            for part in (reply.body[:half], reply.body[half:]):
                self.wfile.write(f"{len(part):x}\r\n".encode() + part + b"\r\n")
            self.wfile.write(b"0\r\n\r\n")
        else:
            self.send_header("Content-Length", str(len(reply.body)))
            self.end_headers()
            self.wfile.write(reply.body)


class FakeTailscaled(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

    def handle_error(self, request, client_address):  # a client that hangs up mid-reply is part of the tests
        pass

    def __init__(self, path: Path):
        self.requests: list[dict] = []
        self.routes: dict[str, Reply] = {}
        super().__init__(str(path), Handler)


def short_dir() -> Path:
    """AF_UNIX paths are limited to about 100 bytes, which pytest's tmp_path can exceed."""
    return Path(tempfile.mkdtemp(prefix="ts"))


def start(directory: Path | None = None) -> FakeTailscaled:
    directory = directory or short_dir()
    server = FakeTailscaled(directory / "tailscaled.sock")
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
    return server
