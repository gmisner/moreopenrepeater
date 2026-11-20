"""`GET /host-facts` on the core's control socket: the kernel log, read when asked, whatever the host gave the core."""
import asyncio
import json
import os
import time
from pathlib import Path

import pytest
from control_helpers import get, make_server, raw

from api.remote_core import RemoteCore
from api.service.core_handle import CoreUnavailable
from contracts import CoreHostFacts
from core import control, host_facts

KMSG = (
    b"6,31797,143820904747,-;usb 1-1.2: Manufacturer: AIOC\n SUBSYSTEM=usb\n DEVICE=c189:5\n"
    b"4,31900,143999000001,-;usb 1-1-port2: disabled by hub (EMI?)\n SUBSYSTEM=usb\n"
)


@pytest.fixture
def kmsg(tmp_path):
    path = tmp_path / "kmsg"
    path.write_bytes(KMSG)
    return path


def serve(core, sock_dir, scenario, **kwargs):
    async def go():
        path = sock_dir / "core.sock"
        server = make_server(core, path, **kwargs)
        await server.start()
        try:
            return await scenario(path)
        finally:
            await server.stop()

    return asyncio.run(go())


def ask(core, sock_dir, **kwargs):
    return serve(core, sock_dir, lambda path: get(path, "/host-facts"), **kwargs)


def test_it_answers_a_core_host_facts_with_the_kernel_lines(core, sock_dir, kmsg):
    status, body = ask(core, sock_dir, kernel_log_path=kmsg)
    assert status == 200 and body["kind"] == "core.host_facts" and body["kernel_error"] is None
    message = CoreHostFacts.model_validate(body)  # the contract's own check
    assert len(message.kernel_log) == 2
    assert message.kernel_log[0].endswith("[143820.904747] usb 1-1.2: Manufacturer: AIOC")
    assert message.kernel_log[1].endswith("usb 1-1-port2: disabled by hub (EMI?)")


def test_the_lines_carry_a_wall_clock_time_from_the_hosts_boot_time(core, sock_dir, kmsg, monkeypatch):
    monkeypatch.setattr(host_facts, "boot_time", lambda *a: 1763551200.0)  # 2025-11-19 11:20:00 UTC
    _, body = ask(core, sock_dir, kernel_log_path=kmsg)
    assert body["kernel_log"][0] == "2025-11-21 03:17:00Z [143820.904747] usb 1-1.2: Manufacturer: AIOC"


def test_a_core_not_given_the_log_says_not_enabled_and_has_no_lines(core, sock_dir):
    status, body = ask(core, sock_dir, kernel_log_path=Path("/dev/null"))
    assert status == 200 and body["kernel_log"] is None and body["kernel_error"] == host_facts.NOT_ENABLED


def test_a_missing_device_is_said_in_words(core, sock_dir, tmp_path):
    _, body = ask(core, sock_dir, kernel_log_path=tmp_path / "gone")
    assert body["kernel_log"] is None and "is not there" in body["kernel_error"]


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read anything")
def test_permission_denied_is_said_in_words(core, sock_dir, kmsg):
    kmsg.chmod(0)
    _, body = ask(core, sock_dir, kernel_log_path=kmsg)
    assert body["kernel_log"] is None and "permission denied" in body["kernel_error"]


def test_it_is_read_when_asked_and_not_before_or_kept(core, sock_dir, kmsg, monkeypatch):
    reads = []
    real = host_facts.read_kernel_log
    monkeypatch.setattr(host_facts, "read_kernel_log", lambda *a, **k: reads.append(1) or real(*a, **k))

    async def scenario(path):
        before = len(reads)
        await get(path, "/status")
        await get(path, "/health")
        quiet = len(reads) - before  # other endpoints never read it
        first = await get(path, "/host-facts")
        kmsg.write_bytes(KMSG + b"6,32000,144500000000,-;usb 1-1.2: new message\n")
        second = await get(path, "/host-facts")  # a fresh read, not a cached one
        return quiet, first, second

    quiet, first, second = serve(core, sock_dir, scenario, kernel_log_path=kmsg)
    assert quiet == 0 and len(reads) == 2
    assert len(first[1]["kernel_log"]) == 2 and len(second[1]["kernel_log"]) == 3


def test_a_read_that_raises_is_an_answer_not_a_failure_of_the_server(core, sock_dir, kmsg, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("secret detail")

    monkeypatch.setattr(host_facts, "read_kernel_log", boom)

    async def scenario(path):
        answer = await get(path, "/host-facts")
        after = await get(path, "/status")  # the server is still serving
        return answer, after

    (status, body), (after_status, _) = serve(core, sock_dir, scenario, kernel_log_path=kmsg)
    assert status == 200 and body["kernel_log"] is None and "failed" in body["kernel_error"]
    assert "secret detail" not in json.dumps(body) and after_status == 200


def test_a_read_that_hangs_is_cut_off_and_does_not_hold_up_the_loop(core, sock_dir, kmsg, monkeypatch):
    monkeypatch.setattr(control, "HOST_FACTS_DEADLINE_SECONDS", 0.2)
    monkeypatch.setattr(host_facts, "read_kernel_log", lambda *a, **k: time.sleep(1.0) or ([], None))

    async def scenario(path):
        started = time.monotonic()
        slow = asyncio.create_task(get(path, "/host-facts"))
        await asyncio.sleep(0.05)
        health_started = time.monotonic()
        health = await get(path, "/health")  # answered while the read is stuck: it is not on the event loop
        health_took = time.monotonic() - health_started
        answer = await slow
        return health, health_took, answer, time.monotonic() - started

    health, health_took, (status, body), took = serve(core, sock_dir, scenario, kernel_log_path=kmsg)
    assert health[0] == 200 and health_took < 0.15
    assert status == 200 and body["kernel_log"] is None and "too long" in body["kernel_error"]
    assert took < 0.9  # at the deadline, not after the stuck read


def test_the_endpoint_takes_get_only(core, sock_dir, kmsg):
    async def scenario(path):
        data = await raw(path, b"POST /host-facts HTTP/1.1\r\nHost: core\r\nContent-Type: application/json\r\nContent-Length: 0\r\n\r\n")
        return data.split(b" ", 2)[1]

    assert serve(core, sock_dir, scenario, kernel_log_path=kmsg) == b"405"


def test_the_default_source_is_dev_kmsg(core, sock_dir):
    server = make_server(core, sock_dir / "x.sock")
    assert server._kernel_log_path == Path("/dev/kmsg") == host_facts.KMSG_PATH


def test_remote_core_reads_it_over_the_socket(core, sock_dir, kmsg):
    async def scenario(path):
        remote = RemoteCore(path)
        facts = await remote.host_facts()
        return facts

    facts = serve(core, sock_dir, scenario, kernel_log_path=kmsg)
    assert isinstance(facts, CoreHostFacts) and len(facts.kernel_log) == 2


def test_remote_core_says_so_when_the_core_cannot_be_reached(sock_dir):
    with pytest.raises(CoreUnavailable, match="not reachable"):
        asyncio.run(RemoteCore(sock_dir / "nothing.sock").host_facts())


def test_remote_core_says_so_when_the_answer_is_not_a_host_facts(core, sock_dir, kmsg, monkeypatch):
    async def odd(self):
        return {"kind": "something.else"}

    monkeypatch.setattr(control.ControlServer, "_host_facts", odd)

    async def scenario(path):
        return await RemoteCore(path).host_facts()

    with pytest.raises(CoreUnavailable, match="not understood"):
        serve(core, sock_dir, scenario, kernel_log_path=kmsg)
