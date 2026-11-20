"""The support bundle's kernel section in the container install: the lines come from the core, which was given the host's /dev/kmsg."""
import asyncio
import subprocess
import threading
import zipfile
from datetime import datetime, timezone

import pytest

from api.remote_host_facts import RemoteHostFacts
from api.routes.admin_ops import _core_host_facts
from api.service.core_handle import CoreUnavailable
from api.support_bundle import KERNEL_LINES, _usb_snapshot, write_support_bundle
from contracts import CoreHostFacts, Header, Source, new_id

LOG = [
    "2025-11-21 03:15:20Z [143820.904747] usb 1-1.2: Manufacturer: AIOC",
    "2025-11-21 03:15:20Z [143820.905000] cpu cpu0: frequency changed",
    "2025-11-21 03:17:01Z [143999.000001] usb 1-1-port2: disabled by hub (EMI?)",
    "2025-11-21 03:17:02Z [144000.000000] eth0: link up",
]


def facts(log=None, error=None):
    header = Header(
        schema_version=1, message_id=new_id(), emitted_at=datetime.now(timezone.utc), boot_id=new_id(), seq=1,
        source=Source(module="core", version="0.1.0"),
    )
    return CoreHostFacts(header=header, kernel_log=log, kernel_error=error)


def no_journal(argv, **kwargs):
    raise FileNotFoundError(argv[0])


def kernel_section(text: str) -> str:
    return text.split("=== kernel (usb/hid, recent) ===")[1]


def test_the_lines_the_core_read_are_used_and_filtered_to_usb_and_hid(tmp_path):
    text = _usb_snapshot(no_journal, tmp_path, core_host_facts=lambda: facts(LOG))
    section = kernel_section(text)
    assert "usb 1-1.2: Manufacturer: AIOC" in section and "disabled by hub (EMI?)" in section
    assert "cpu cpu0" not in section and "eth0" not in section  # the filter is the same one the journal gets
    assert "reads the host's /dev/kmsg" in section and "not visible from here" not in section


def test_the_core_s_lines_are_used_without_trying_journalctl(tmp_path):
    asked = []

    def run(argv, **kwargs):
        asked.append(argv[0])
        raise FileNotFoundError(argv[0])

    _usb_snapshot(run, tmp_path, core_host_facts=lambda: facts(LOG))
    assert "journalctl" not in asked


def test_a_core_log_with_nothing_usb_in_it_says_so_and_not_that_it_is_unavailable(tmp_path):
    section = kernel_section(_usb_snapshot(no_journal, tmp_path, core_host_facts=lambda: facts(["[1.0] eth0: link up"])))
    assert "(no usb/hid lines in recent kernel log)" in section and "not visible" not in section


def test_an_empty_log_from_the_core_is_not_taken_for_a_missing_one(tmp_path):
    section = kernel_section(_usb_snapshot(no_journal, tmp_path, core_host_facts=lambda: facts([])))
    assert "(no usb/hid lines in recent kernel log)" in section and "not visible" not in section


@pytest.mark.parametrize("reason", [
    "the host's kernel log was not given to the core (MOR_KMSG_DEVICE)",
    "permission denied reading /dev/kmsg: the host may restrict it (kernel.dmesg_restrict=1)",
])
def test_when_the_core_has_none_the_old_text_stays_and_the_reason_is_added(tmp_path, reason):
    section = kernel_section(_usb_snapshot(no_journal, tmp_path, core_host_facts=lambda: facts(error=reason)))
    assert "the host's kernel log is not visible from here; on the host: dmesg -T | grep -i usb" in section
    assert reason in section and "asked the core, which has none" in section
    assert "(no usb/hid lines" not in section  # not claiming there was a log with nothing in it


def test_when_the_core_cannot_be_asked_that_is_the_reason(tmp_path):
    def down():
        raise CoreUnavailable("the core is not reachable, so its view of the host cannot be read")

    section = kernel_section(_usb_snapshot(no_journal, tmp_path, core_host_facts=down))
    assert "not visible from here" in section and "could not ask the core for it: the core is not reachable" in section


def test_a_core_call_that_blows_up_does_not_stop_the_bundle(tmp_path):
    def broken():
        raise ValueError("whatever")

    section = kernel_section(_usb_snapshot(no_journal, tmp_path, core_host_facts=broken))
    assert "could not ask the core for it: whatever" in section and "dmesg -T" in section


def test_without_a_core_to_ask_it_is_exactly_what_it_was(tmp_path):
    def run(argv, **kwargs):
        if argv[:2] == ["journalctl", "-k"]:
            return subprocess.CompletedProcess(argv, 0, "usb 1-1.2: USB disconnect\nunrelated line\n", "")
        raise FileNotFoundError(argv[0])

    section = kernel_section(_usb_snapshot(run, tmp_path))
    assert "usb 1-1.2: USB disconnect" in section and "unrelated line" not in section and "from the core" not in section
    gone = kernel_section(_usb_snapshot(no_journal, tmp_path))
    assert "not visible from here" in gone and "asked the core" not in gone and "could not ask" not in gone


def test_where_journalctl_works_and_the_core_has_none_the_journal_is_used(tmp_path):
    def run(argv, **kwargs):
        if argv[:2] == ["journalctl", "-k"]:
            return subprocess.CompletedProcess(argv, 0, "usb 9-9: from the journal\n", "")
        raise FileNotFoundError(argv[0])

    section = kernel_section(_usb_snapshot(run, tmp_path, core_host_facts=lambda: facts(error="not enabled")))
    assert "usb 9-9: from the journal" in section


def test_the_whole_bundle_carries_the_core_s_kernel_lines(tmp_path):
    from api.persistence import StateStore
    from api.service import RepeaterService

    service = RepeaterService(state_store=StateStore(tmp_path / "state.json"))
    log = tmp_path / "app.log"
    log.write_text("x\n")
    path = tmp_path / "bundle.zip"
    write_support_bundle(
        path, service=service, log_path=log, repo_dir=tmp_path, update_channel="dev", live_audio_status={}, system_readings={},
        updater=None, now=1_700_000_000.0, core_host_facts=lambda: facts(LOG), run=no_journal,
    )
    with zipfile.ZipFile(path) as archive:
        usb = archive.read("usb.txt").decode()
    assert "disabled by hub (EMI?)" in usb and "eth0: link up" not in usb


def test_the_kernel_log_is_asked_for_the_same_number_of_lines_as_the_journal():
    from core import host_facts

    assert KERNEL_LINES == host_facts.KERNEL_LINES == 400


# -- the thread bridge the route uses -------------------------------------------------------------------------------------

class LoopThread:
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)
        self.loop.close()


@pytest.fixture
def loop():
    thread = LoopThread()
    yield thread
    thread.close()


class FakeRemote:
    def __init__(self, loop, result=None, error=None, delay=0.0):
        self.loop = loop.loop if loop is not None else None
        self.result, self.error, self.delay = result, error, delay

    async def host_facts(self):
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return self.result


def test_a_worker_thread_gets_the_cores_answer_through_the_loop(loop):
    answer = facts(LOG)
    assert RemoteHostFacts(FakeRemote(loop, answer))() is answer


def test_the_core_being_unreachable_reaches_the_caller_as_it_is(loop):
    with pytest.raises(CoreUnavailable):
        RemoteHostFacts(FakeRemote(loop, error=CoreUnavailable("down")))()


def test_a_slow_core_is_a_timeout_not_a_wait_forever(loop):
    with pytest.raises(TimeoutError):
        RemoteHostFacts(FakeRemote(loop, facts(LOG), delay=2.0), timeout=0.1)()


def test_before_the_core_was_reached_it_says_so():
    with pytest.raises(RuntimeError, match="not been reached"):
        RemoteHostFacts(FakeRemote(None))()


def test_it_cannot_be_called_on_the_loop_it_would_wait_on(loop):
    async def on_the_loop():
        RemoteHostFacts(FakeRemote(loop, facts(LOG)))()

    with pytest.raises(RuntimeError, match="event loop's own thread"):
        asyncio.run_coroutine_threadsafe(on_the_loop(), loop.loop).result(5)


def test_the_route_asks_the_core_only_when_the_core_is_another_process():
    class Remote:
        is_remote = True

    assert isinstance(_core_host_facts(Remote()), RemoteHostFacts)
    assert _core_host_facts(object()) is None


def test_the_download_route_hands_the_bundle_the_core_question_only_for_a_remote_core(monkeypatch):
    from api.routes import admin_ops
    from test_support_bundle import Repeater

    seen = {}

    def spy(path, **kwargs):
        seen.update(kwargs)
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("README.txt", "x")

    monkeypatch.setattr(admin_ops, "write_support_bundle", spy)
    sentinel = object()
    monkeypatch.setattr(admin_ops, "_core_host_facts", lambda service: sentinel)
    assert Repeater().client.get("/api/support-bundle").status_code == 200
    assert seen["core_host_facts"] is sentinel
    monkeypatch.undo()
    monkeypatch.setattr(admin_ops, "write_support_bundle", spy)
    assert Repeater().client.get("/api/support-bundle").status_code == 200
    assert seen["core_host_facts"] is None  # an in-process core: the host's own log is read here
