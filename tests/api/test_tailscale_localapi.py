"""The LocalAPI client against a fake tailscaled on a real Unix socket (container install, no CLI)."""
import json
import os
import socket
import threading
import time

import pytest

import fake_tailscaled as fake
from api.tailscale_localapi import (
    DEFAULT_SOCKET,
    LocalApiError,
    get_json,
    read_state,
    socket_path_from_env,
)


@pytest.fixture
def tailscaled():
    server = fake.start()
    server.routes["/localapi/v0/status"] = fake.Reply(body=json.dumps(fake.STATUS).encode())
    server.routes["/localapi/v0/serve-config"] = fake.Reply(body=json.dumps(fake.SERVE).encode())
    yield server
    server.shutdown()
    server.server_close()


def path_of(server):
    return server.server_address


def reason_of(call):
    with pytest.raises(LocalApiError) as caught:
        call()
    return caught.value.reason, str(caught.value)


def test_status_and_serve_config_are_read_the_way_the_cli_prints_them(tailscaled):
    status, serve = read_state(path_of(tailscaled))
    assert status["BackendState"] == "Running" and status["Self"]["DNSName"] == "pi.tail123.ts.net."
    assert serve == fake.SERVE


def test_it_sends_what_tailscaled_requires_and_nothing_else(tailscaled):
    read_state(path_of(tailscaled))
    assert [(r["method"], r["path"].split("?")[0]) for r in tailscaled.requests] == [
        ("GET", "/localapi/v0/status"),
        ("GET", "/localapi/v0/serve-config"),
    ]
    for request in tailscaled.requests:
        headers = {k.lower(): v for k, v in request["headers"].items()}
        assert headers["host"] == "local-tailscaled.sock"  # tailscaled refuses any other Host
        assert "origin" not in headers and "referer" not in headers


def test_it_only_ever_reads(tailscaled):
    """The dashboard must not gain the ability to change Tailscale: every request is a GET."""
    read_state(path_of(tailscaled))
    with pytest.raises(LocalApiError):
        get_json("/localapi/v0/nope", path_of(tailscaled))
    assert {r["method"] for r in tailscaled.requests} == {"GET"}


def test_a_chunked_reply_is_read_whole(tailscaled):
    """Go's server streams a big JSON body without a Content-Length."""
    tailscaled.routes["/localapi/v0/status"] = fake.Reply(body=json.dumps(fake.STATUS).encode(), chunked=True)
    status, _ = read_state(path_of(tailscaled))
    assert status["BackendState"] == "Running"


def test_access_denied_is_said_as_such(tailscaled):
    tailscaled.routes["/localapi/v0/status"] = fake.Reply(403, b"status access denied")
    reason, message = reason_of(lambda: read_state(path_of(tailscaled)))
    assert reason == "denied" and "refused" in message


def test_a_status_that_is_not_there_is_not_found(tailscaled):
    del tailscaled.routes["/localapi/v0/status"]
    assert reason_of(lambda: read_state(path_of(tailscaled)))[0] == "not_found"


@pytest.mark.parametrize("reply", [fake.Reply(404, b"404 page not found"), fake.Reply(403, b"serve config denied"), fake.Reply(500, b"boom"), fake.Reply(body=b"{oops")])
def test_a_serve_config_that_cannot_be_read_leaves_serve_off_but_keeps_the_status(tailscaled, reply):
    tailscaled.routes["/localapi/v0/serve-config"] = reply
    status, serve = read_state(path_of(tailscaled))
    assert status["BackendState"] == "Running" and serve is None


@pytest.mark.parametrize("body", [b"null", b"{}", b"[]"])
def test_an_empty_serve_config_is_off(tailscaled, body):
    tailscaled.routes["/localapi/v0/serve-config"] = fake.Reply(body=body)
    assert read_state(path_of(tailscaled))[1] in (None, {})


@pytest.mark.parametrize("body", [b"not json", b"", b"[1, 2]", b'"text"', b"null"])
def test_a_status_that_is_not_a_json_object_is_a_bad_reply(tailscaled, body):
    tailscaled.routes["/localapi/v0/status"] = fake.Reply(body=body)
    assert reason_of(lambda: read_state(path_of(tailscaled)))[0] == "bad_reply"


def test_an_unexpected_http_status_is_a_bad_reply(tailscaled):
    tailscaled.routes["/localapi/v0/status"] = fake.Reply(500, b"internal error")
    reason, message = reason_of(lambda: read_state(path_of(tailscaled)))
    assert reason == "bad_reply" and "500" in message


def test_a_runaway_reply_is_cut_off(tailscaled):
    # Valid JSON that is padded past the limit: cut at the limit it still parses, so only the size check can refuse it.
    tailscaled.routes["/localapi/v0/status"] = fake.Reply(body=b"{}" + b" " * (3 * 1024 * 1024))
    assert reason_of(lambda: read_state(path_of(tailscaled)))[0] == "bad_reply"


def test_a_server_that_never_answers_gives_up_quickly(tailscaled):
    tailscaled.routes["/localapi/v0/status"] = fake.Reply(hang=True)
    outcome = {}

    def call():
        outcome["result"] = reason_of(lambda: read_state(path_of(tailscaled), timeout=0.3))

    started = time.monotonic()
    worker = threading.Thread(target=call, daemon=True)  # a regression must fail this test, not hang the suite
    worker.start()
    worker.join(5)
    assert not worker.is_alive(), "no timeout: the call is still waiting for a server that never answers"
    assert time.monotonic() - started < 3
    reason, message = outcome["result"]
    assert reason == "timeout" and "didn't answer" in message


def test_the_default_timeout_is_short_enough_for_a_page_that_polls():
    from api import tailscale_localapi

    assert tailscale_localapi.TIMEOUT_SECONDS <= 3


def test_a_missing_socket_says_where_it_looked_and_how_to_fix_it():
    missing = str(fake.short_dir() / "tailscaled.sock")
    reason, message = reason_of(lambda: read_state(missing))
    assert reason == "missing"
    assert missing in message and "MOR_TAILSCALE_DIR" in message and "isn't installed" not in message.lower()


def test_a_socket_folder_that_is_not_there_is_missing_too():
    assert reason_of(lambda: read_state(str(fake.short_dir() / "no" / "such" / "tailscaled.sock")))[0] == "missing"


@pytest.mark.skipif(os.geteuid() == 0, reason="root can open anything")
def test_a_socket_this_user_cannot_open_says_permission_not_missing(tailscaled):
    os.chmod(path_of(tailscaled), 0o000)
    reason, message = reason_of(lambda: read_state(path_of(tailscaled)))
    assert reason == "not_readable"
    assert "permission denied" in message and "user 999" in message and path_of(tailscaled) in message


def test_a_socket_file_nobody_listens_on_says_so():
    path = str(fake.short_dir() / "t.sock")
    dead = socket.socket(socket.AF_UNIX)
    dead.bind(path)
    dead.close()  # the file stays, as tailscaled's does after it dies
    reason, message = reason_of(lambda: read_state(path))
    assert reason == "not_listening" and "stopped" in message


def test_the_socket_path_comes_from_the_environment_with_the_documented_default():
    assert DEFAULT_SOCKET == "/var/run/tailscale/tailscaled.sock"
    assert socket_path_from_env({}) == DEFAULT_SOCKET
    assert socket_path_from_env({"MOREOPENREPEATER_TAILSCALE_SOCKET": "  "}) == DEFAULT_SOCKET
    assert socket_path_from_env({"MOREOPENREPEATER_TAILSCALE_SOCKET": "/run/ts/x.sock"}) == "/run/ts/x.sock"
