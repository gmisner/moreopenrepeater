"""The on-disk file of errors for installs that keep the history in memory (#221, #229)."""
import asyncio
import json
import os
import shutil
import socket
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from contracts import LogEvent, Severity
from logd.critical import FILE_NAME, CriticalFile
from logd.main import Settings, restore_critical, serve, settings_from_env
from logd.server import Collector
from logd.store import EventStore

BOOT = "b0000000000000000000000000000001"


@pytest.fixture
def folder():
    path = Path(tempfile.mkdtemp(prefix="mor-", dir="/tmp"))
    yield path
    shutil.rmtree(path, ignore_errors=True)


def make(seq, severity=Severity.ERROR, message="x", boot=BOOT):
    return LogEvent(
        time=datetime.now(timezone.utc), boot_id=boot, seq=seq, severity=severity, event="test.event", module="core",
        module_version="0.1.0", message=message, context={"n": seq},
    )


def test_only_errors_and_worse_are_written(folder):
    f = CriticalFile(folder)
    f.append([make(0, Severity.INFO), make(1, Severity.WARNING), make(2, Severity.ERROR), make(3, Severity.CRITICAL)])
    assert [e.seq for e in f.load()] == [2, 3]
    assert f.written == 2


def test_nothing_is_created_until_there_is_an_error(folder):
    f = CriticalFile(folder / "later")
    f.append([make(0, Severity.INFO)])
    assert not (folder / "later").exists()


def test_it_is_bounded_by_keeping_one_older_file(folder):
    f = CriticalFile(folder, max_bytes=2000)
    for i in range(200):
        f.append([make(i, message="m" * 100)])
    size = sum(p.stat().st_size for p in folder.iterdir())
    assert size < 2000 * 2 + 600  # the current file and one older one, each at most about the limit
    assert {p.name for p in folder.iterdir()} == {FILE_NAME, FILE_NAME + ".1"}
    seqs = [e.seq for e in f.load()]
    assert seqs == sorted(seqs) and seqs[-1] == 199  # the newest are kept, in order
    assert seqs[0] > 0  # the oldest went


def test_a_damaged_or_half_written_line_is_skipped_and_the_rest_read(folder):
    f = CriticalFile(folder)
    f.append([make(0), make(1)])
    with open(f.path, "a") as handle:
        handle.write("not json\n" + '{"time": "2026-11-18T12:00:00Z", "boot_id"')  # a power cut mid-write
    f.append([make(2)])
    assert [e.seq for e in f.load()] == [0, 1, 2]


def test_a_write_that_fails_is_counted_and_never_raised(folder, caplog):
    blocker = folder / "blocked"
    blocker.write_text("a file where the folder should be")
    f = CriticalFile(blocker)
    f.append([make(0)])
    f.append([make(1)])
    assert f.write_errors == 2 and f.written == 0
    assert len([r for r in caplog.records if "critical events file" in r.getMessage()]) == 1  # said once


def test_stats_say_how_much_is_kept(folder):
    f = CriticalFile(folder, max_bytes=5000)
    f.append([make(0)])
    s = f.stats()
    assert s["bytes"] > 0 and s["max_bytes"] == 10000 and s["written"] == 1 and s["restored"] == 0


# -- coming back after a restart --------------------------------------------------------------------------


def test_an_empty_history_gets_the_errors_back(folder):
    CriticalFile(folder).append([make(0), make(1, Severity.CRITICAL, "disk gone")])
    fresh = EventStore(None)  # the in-memory history after the machine restarted
    f = CriticalFile(folder)
    assert restore_critical(fresh, f) == 2
    page = fresh.query(min_severity=40)
    assert [e["message"] for e in page["events"]] == ["disk gone", "x"]
    assert f.restored == 2


def test_a_history_that_has_events_is_not_given_them_twice(folder):
    CriticalFile(folder).append([make(0)])
    store = EventStore(None)
    store.insert_many([make(5, Severity.INFO)])
    assert restore_critical(store, CriticalFile(folder)) == 0
    assert store.stats()["events"] == 1


def test_a_missing_file_restores_nothing_and_does_not_fail(folder):
    assert restore_critical(EventStore(None), CriticalFile(folder / "nothing")) == 0


def test_a_module_that_comes_back_after_a_restore_is_a_restart_not_lost_events(folder):
    CriticalFile(folder).append([make(7)])
    store = EventStore(None)
    restore_critical(store, CriticalFile(folder))
    store.insert_many([make(0, Severity.INFO, boot="b0000000000000000000000000000002")])
    kinds = [g["kind"] for g in store.gaps()]
    assert kinds == ["restart"]


# -- the collector ----------------------------------------------------------------------------------------


def test_the_collector_writes_errors_even_when_the_store_fails(folder):
    class BrokenStore(EventStore):
        def insert_many(self, events):
            raise OSError("disk full")

    store = BrokenStore(None)
    critical = CriticalFile(folder / "crit")
    collector = Collector(store, folder / "e.sock", critical=critical)
    collector._queue.extend([make(0, Severity.INFO), make(1, Severity.ERROR, "kept")])
    asyncio.run(collector._drain())
    assert collector.write_errors == 1
    assert [e.message for e in critical.load()] == ["kept"]


def test_without_a_critical_file_the_collector_writes_none(folder):
    collector = Collector(EventStore(None), folder / "e.sock")
    collector._queue.append(make(0))
    asyncio.run(collector._drain())
    assert collector.written == 1 and not list(folder.iterdir())


# -- settings and the whole service -----------------------------------------------------------------------


def test_the_critical_folder_is_off_unless_set():
    assert settings_from_env({}).critical_dir is None
    assert settings_from_env({"MOREOPENREPEATER_LOGD_CRITICAL_DIR": "  "}).critical_dir is None
    assert settings_from_env({"MOREOPENREPEATER_LOGD_CRITICAL_DIR": "/var/lib/c"}).critical_dir == Path("/var/lib/c")


def _settings(run, memory, critical):
    return Settings(
        events_socket=run / "e.sock", query_socket=run / "q.sock", data_dir=memory, max_bytes=8 * 1024 * 1024,
        routine_days=14, critical_days=30, critical_dir=critical,
    )


async def _ask(path, target):
    reader, writer = await asyncio.open_unix_connection(str(path))
    writer.write(f"GET {target} HTTP/1.1\r\nHost: logd\r\n\r\n".encode())
    await writer.drain()
    data = await asyncio.wait_for(reader.read(), 5)
    writer.close()
    return json.loads(data.partition(b"\r\n\r\n")[2])


def test_an_error_survives_the_history_being_lost_and_the_stats_say_the_history_is_in_memory(folder):
    critical = folder / "disk"

    async def one_life(memory, send=None):
        s = _settings(folder, memory, critical)
        stop = asyncio.Event()
        task = asyncio.create_task(serve(s, stop))
        for _ in range(200):
            if s.query_socket.exists() and s.events_socket.exists():
                break
            await asyncio.sleep(0.02)
        if send:
            sender = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            for event in send:
                sender.sendto(event.to_json_line().encode(), str(s.events_socket))
            sender.close()
        body = {}
        for _ in range(100):
            body = await _ask(s.query_socket, "/events")
            if not send or len(body["events"]) >= len(send):
                break
            await asyncio.sleep(0.1)
        stats = await _ask(s.query_socket, "/stats")
        stop.set()
        await asyncio.wait_for(task, 10)
        return body, stats

    first, stats = asyncio.run(one_life(folder / "ram1", [make(0, Severity.INFO, "chatter"), make(1, Severity.ERROR, "ptt failed")]))
    assert len(first["events"]) == 2
    assert stats["volatile"] is True and stats["critical_file"]["written"] == 1
    # The machine restarts: the memory is empty, the disk is not.
    second, stats = asyncio.run(one_life(folder / "ram2"))
    assert [e["message"] for e in second["events"]] == ["ptt failed"]
    assert stats["critical_file"]["restored"] == 1


def test_without_the_folder_the_history_is_not_volatile(folder):
    async def go():
        s = _settings(folder, folder / "data", None)
        stop = asyncio.Event()
        task = asyncio.create_task(serve(s, stop))
        for _ in range(200):
            if s.query_socket.exists():
                break
            await asyncio.sleep(0.02)
        stats = await _ask(s.query_socket, "/stats")
        stop.set()
        await asyncio.wait_for(task, 10)
        return stats

    stats = asyncio.run(go())
    assert stats["volatile"] is False and stats["critical_file"] is None
