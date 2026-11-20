"""Read Tailscale's state over its LocalAPI, for an API that has no `tailscale` command.

In the container install the API image carries no Tailscale CLI and holds no host access, so the Remote page could not
tell whether the host runs Tailscale. tailscaled, though, talks HTTP over a Unix socket (by default
/var/run/tailscale/tailscaled.sock), and compose can show the API that socket's folder read-only
(docs/docker.md, Tailscale). This module is the small client for it.

It only ever sends GET, and only to two paths:

  /localapi/v0/status        what `tailscale status --json` prints
  /localapi/v0/serve-config  what `tailscale serve status --json` prints

Both facts are from Tailscale's own source (ipn/localapi, ipn/ipnserver). Any local user that can open the socket may
read; changing anything needs root or the configured operator, and the API's user is neither, so tailscaled would answer
403 to a write even if this code tried one. A request carries the Host header `local-tailscaled.sock` (tailscaled
rejects other hosts) and no Origin or Referer.
"""
from __future__ import annotations

import errno
import http.client
import json
import socket
from typing import Any, Optional

DEFAULT_SOCKET = "/var/run/tailscale/tailscaled.sock"
LOCALAPI_HOST = "local-tailscaled.sock"
STATUS_PATH = "/localapi/v0/status?peers=false"
SERVE_PATH = "/localapi/v0/serve-config"
TIMEOUT_SECONDS = 2.0
MAX_BODY_BYTES = 2 * 1024 * 1024  # a status without peers is a few KB; this only stops a runaway reply


def socket_path_from_env(env: dict) -> str:
    return (env.get("MOREOPENREPEATER_TAILSCALE_SOCKET") or "").strip() or DEFAULT_SOCKET


class LocalApiError(Exception):
    """Why Tailscale couldn't be read. `reason` is one of missing, not_readable, not_listening, timeout, denied,
    not_found, bad_reply; `str(error)` is a sentence for the dashboard."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


class _UnixConnection(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float) -> None:
        super().__init__(LOCALAPI_HOST, timeout=timeout)
        self._path = path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            sock.connect(self._path)
        except BaseException:
            sock.close()
            raise
        self.sock = sock


def _connect_error(path: str, error: OSError) -> LocalApiError:
    if error.errno in (errno.EACCES, errno.EPERM):
        return LocalApiError(
            "not_readable",
            f"Tailscale's socket at {path} is there, but this dashboard isn't allowed to open it (permission denied). "
            "The dashboard runs as user 999, so the socket has to be readable by other users (docs/docker.md, Tailscale).",
        )
    if error.errno in (errno.ENOENT, errno.ENOTDIR):
        return LocalApiError(
            "missing",
            f"There is no Tailscale socket at {path}. Tailscale may not be installed or running on the host, or the "
            "dashboard's container wasn't given the host's socket folder (MOR_TAILSCALE_DIR, docs/docker.md, Tailscale).",
        )
    if error.errno in (errno.ECONNREFUSED, errno.ECONNRESET):
        return LocalApiError(
            "not_listening",
            f"Tailscale's socket at {path} is there, but nothing is answering on it. Tailscale on the host is probably stopped.",
        )
    return LocalApiError("bad_reply", f"Couldn't open Tailscale's socket at {path}: {error.strerror or error}.")


def get_json(path: str, socket_path: str, timeout: float = TIMEOUT_SECONDS) -> Any:
    """GET `path` from tailscaled's socket and return the decoded JSON, or raise LocalApiError."""
    connection = _UnixConnection(socket_path, timeout)
    try:
        try:
            connection.request("GET", path, headers={"Accept": "application/json"})
            response = connection.getresponse()
            body = response.read(MAX_BODY_BYTES + 1)
        except (TimeoutError, socket.timeout):
            raise LocalApiError("timeout", f"Tailscale didn't answer within {timeout:g} seconds.") from None
        except OSError as error:
            raise _connect_error(socket_path, error) from None
        except http.client.HTTPException:
            raise LocalApiError("bad_reply", "Tailscale's answer wasn't a valid HTTP reply.") from None
    finally:
        connection.close()
    if response.status == 403:
        raise LocalApiError("denied", "Tailscale answered but refused to share its state with this dashboard.")
    if response.status == 404:
        raise LocalApiError("not_found", "Tailscale doesn't have that.")
    if response.status != 200:
        raise LocalApiError("bad_reply", f"Tailscale answered with an unexpected status ({response.status}).")
    if len(body) > MAX_BODY_BYTES:
        raise LocalApiError("bad_reply", "Tailscale's answer was far larger than expected.")
    try:
        return json.loads(body)
    except ValueError:
        raise LocalApiError("bad_reply", "Tailscale's answer wasn't valid JSON.") from None


def read_state(socket_path: str, timeout: float = TIMEOUT_SECONDS) -> tuple[dict, Optional[dict]]:
    """(status, serve config) as the two CLI commands would give them. Raises LocalApiError if the status can't be read.

    A serve config that can't be read leaves Serve looking off, the same as the CLI path when its second command fails.
    An empty config comes back from tailscaled as JSON null or an empty object."""
    status = get_json(STATUS_PATH, socket_path, timeout)
    if not isinstance(status, dict):
        raise LocalApiError("bad_reply", "Tailscale's status wasn't what was expected.")
    try:
        serve = get_json(SERVE_PATH, socket_path, timeout)
    except LocalApiError:
        return status, None
    return status, serve if isinstance(serve, dict) else None
