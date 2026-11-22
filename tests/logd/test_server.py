"""The collector's sockets (#221, #229): events in as datagrams, searches out over a Unix socket."""
import asyncio
import json
import logging
import os
import shutil
import socket
import stat
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from contracts import LogEvent, Severity
from logd.server import Collector, QueryServer
from logd.store import EventStore
from observability.events import ProcessIdentity, log_event
from observability.handlers import CollectorHandler

BOOT = "b0000000000000000000000000000001"


@pytest.fixture
def run_dir():
    path = Path(tempfile.mkdtemp(prefix="mor-", dir="/tmp"))  # AF_UNIX paths are short
    yield path
    shutil.rmtree(path, ignore_errors=True)


def make_event(seq, message="hello", severity=Severity.INFO, name="test.event", module="core", **context):
    return LogEvent(
        time=datetime.now(timezone.utc), boot_id=BOOT, seq=seq, severity=severity, event=name, module=module,
        module_version="0.1.0", message=message, context=context,
    )


def send(path, data):
    sender = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        sender.sendto(data if isinstance(data, bytes) else data.to_json_line().encode(), str(path))
    finally:
        sender.close()


async def until(condition, timeout=5.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not condition():
        assert asyncio.get_running_loop().time() < deadline, "timed out"
        await asyncio.sleep(0.01)


async def get(path, target):
    reader, writer = await asyncio.open_unix_connection(str(path))
    writer.write(f"GET {target} HTTP/1.1\r\nHost: logd\r\n\r\n".encode())
    await writer.drain()
    data = await asyncio.wait_for(reader.read(), 10)
    writer.close()
    head, _, body = data.partition(b"\r\n\r\n")
    return int(head.split(b" ", 2)[1]), (json.loads(body) if body else None)


class Running:
    """A collector and a query server on a store, for a test."""

    def __init__(self, run_dir, store=None, **kwargs):
        self.store = store or EventStore(None)
        self.events = run_dir / "events.sock"
        self.query = run_dir / "query.sock"
        kwargs.setdefault("flush_seconds", 0.05)
        self.collector = Collector(self.store, self.events, **kwargs)
        self.http = QueryServer(self.collector, self.query)

    async def __aenter__(self):
        await self.collector.start()
        await self.http.start()
        return self

    async def __aexit__(self, *exc):
        await self.http.stop()
        await self.collector.stop()
        self.store.close()


# -- events in ---------------------------------------------------------------------------------------------


def test_an_event_sent_by_the_real_sender_is_stored_and_found(run_dir):
    async def go():
        async with Running(run_dir) as r:
            log = logging.getLogger("moreopenrepeater.test_logd_e2e")
            log.setLevel(logging.DEBUG)
            log.propagate = False
            handler = CollectorHandler(ProcessIdentity("core", "0.1.0"), r.events)
            log.addHandler(handler)
            try:
                log_event(log, Severity.WARNING, "audio.glitch", "sound card ran dry", error_code="audio.overrun", count=2)
                await until(lambda: r.store.stats()["events"] == 1)
                status, body = await get(r.query, "/events?event=audio.glitch")
            finally:
                log.removeHandler(handler)
                handler.close()
            return status, body

    status, body = asyncio.run(go())
    event = body["events"][0]
    assert status == 200 and event["event"] == "audio.glitch" and event["severity"] == 30
    assert event["context"] == {"count": 2} and event["error_code"] == "audio.overrun" and event["module"] == "core"


def test_batches_are_written_together_and_soon(run_dir):
    async def go():
        async with Running(run_dir, batch_size=50, flush_seconds=10) as r:  # the timer is far off: size must trigger
            for i in range(120):
                send(r.events, make_event(i))
            await until(lambda: r.store.stats()["events"] >= 100)
            return r.collector.stats()["written"]

    assert asyncio.run(go()) >= 100


def test_garbage_is_counted_and_the_rest_carries_on(run_dir):
    async def go():
        async with Running(run_dir, max_datagram=1000) as r:
            send(r.events, b"not json at all")
            send(r.events, b'{"schema_version": 1}')  # not a LogEvent
            send(r.events, b"x" * 1500)  # bigger than this collector allows (macOS cannot send more than 2 KB at all)
            send(r.events, make_event(0, message="fine"))
            await until(lambda: r.store.stats()["events"] == 1)
            await until(lambda: r.collector.stats()["rejected"] == 3)
            return r.collector.stats(), r.store.query()["events"][0]["message"]

    stats, message = asyncio.run(go())
    assert stats["received"] == 4 and stats["rejected"] == 3 and message == "fine"
    assert stats["last_rejected"]  # says why, for whoever looks


def test_when_the_queue_is_full_events_are_dropped_and_counted_and_the_gap_is_recorded(run_dir):
    async def go():
        store = EventStore(None)
        release = asyncio.Event()
        real = store.insert_many
        blocker = []

        def slow(events):
            while not blocker:  # the writer is stuck until released
                time.sleep(0.01)
            return real(events)

        store.insert_many = slow
        async with Running(run_dir, store=store, queue_size=10, batch_size=5) as r:
            for i in range(80):
                send(r.events, make_event(i))
            await until(lambda: r.collector.stats()["received"] == 80)
            dropped = r.collector.stats()["dropped_queue"]
            blocker.append(1)  # let the writer go
            await until(lambda: any(g["kind"] == "collector_overflow" for g in store.gaps()))
            release.set()
            return dropped, [g for g in store.gaps() if g["kind"] == "collector_overflow"][0]

    dropped, gap = asyncio.run(go())
    assert dropped >= 60 and gap["count"] >= 60 and gap["module"] == "logd"


def test_a_failing_write_is_counted_and_the_next_batch_still_goes_in(run_dir):
    async def go():
        store = EventStore(None)
        real = store.insert_many
        calls = []

        def flaky(events):
            calls.append(len(events))
            if len(calls) == 1:
                raise OSError("disk full")
            return real(events)

        store.insert_many = flaky
        async with Running(run_dir, store=store) as r:
            send(r.events, make_event(0, message="lost to the full disk"))
            await until(lambda: r.collector.stats()["write_errors"] == 1)
            send(r.events, make_event(1, message="stored after"))
            await until(lambda: store.stats()["events"] == 1)
            return r.collector.stats(), store.query()["events"][0]["message"]

    stats, message = asyncio.run(go())
    assert stats["write_errors"] == 1 and stats["dropped_write"] == 1 and message == "stored after"


def test_a_critical_file_that_raises_something_other_than_oserror_does_not_stop_the_writer(run_dir):
    class Exploding:
        calls = 0

        def append(self, events):
            Exploding.calls += 1
            if Exploding.calls == 1:
                raise ValueError("not a disk problem")

    async def go():
        store = EventStore(None)
        async with Running(run_dir, store=store, critical=Exploding()) as r:
            send(r.events, make_event(0, message="first", severity=Severity.ERROR))
            await until(lambda: r.collector.stats()["critical_errors"] == 1)
            assert r.collector.health() == "degraded"  # it says so while the file is failing
            send(r.events, make_event(1, message="second"))
            await until(lambda: store.stats()["events"] == 2)  # the store write was not skipped, nor the loop ended
            await until(lambda: r.collector.health() == "ok")  # and the next good pass clears it
            return r.collector.stats(), [t.done() for t in r.collector._tasks]

    stats, done = asyncio.run(go())
    assert stats["critical_errors"] == 1 and stats["written"] == 2 and done == [False, False]


def test_a_store_that_raises_once_does_not_end_the_writer_and_the_events_after_it_are_kept(run_dir):
    async def go():
        store = EventStore(None)
        real = store.insert_many
        calls = []

        def flaky(events):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("odd failure")  # not an OSError
            return real(events)

        store.insert_many = flaky
        async with Running(run_dir, store=store) as r:
            send(r.events, make_event(0, message="lost"))
            await until(lambda: r.collector.stats()["write_errors"] == 1)
            send(r.events, make_event(1, message="kept"))
            await until(lambda: store.stats()["events"] == 1)
            return r.collector.stats(), r.collector._tasks[0].done()

    stats, writer_done = asyncio.run(go())
    assert stats["dropped_write"] == 1 and stats["written"] == 1 and not writer_done


def test_an_error_that_escapes_a_drain_is_counted_and_reported_and_the_writer_goes_on(run_dir):
    async def go():
        store = EventStore(None)
        async with Running(run_dir, store=store) as r:
            real = r.collector._drain
            raised = []

            async def once():
                if not raised:
                    raised.append(1)
                    raise RuntimeError("something unexpected")
                await real()

            r.collector._drain = once
            send(r.events, make_event(0, message="first"))
            await until(lambda: r.collector.stats()["writer_errors"] == 1)
            degraded = r.collector.health()
            send(r.events, make_event(1, message="second"))
            await until(lambda: store.stats()["events"] == 2)
            await until(lambda: r.collector.health() == "ok")
            return degraded, r.collector._tasks[0].done()

    degraded, writer_done = asyncio.run(go())
    assert degraded == "degraded" and not writer_done


def test_stopping_still_drains_when_a_task_had_already_died(run_dir):
    async def go():
        store = EventStore(None)
        r = Running(run_dir, store=store, flush_seconds=30)
        await r.collector.start()

        async def dead():
            raise RuntimeError("died long ago")

        task = asyncio.create_task(dead())
        await asyncio.sleep(0)
        r.collector._tasks.append(task)
        for i in range(5):
            send(r.events, make_event(i))
        await until(lambda: r.collector.stats()["received"] == 5)
        await r.collector.stop()  # must not raise
        events = store.stats()["events"]
        store.insert_many([make_event(99)])  # the store is still open, so the caller can close it
        return events

    assert asyncio.run(go()) == 5


def test_stopping_writes_what_is_still_queued(run_dir):
    async def go():
        store = EventStore(None)
        r = Running(run_dir, store=store, flush_seconds=30)
        await r.collector.start()
        for i in range(10):
            send(r.events, make_event(i))
        await until(lambda: r.collector.stats()["received"] == 10)
        await r.collector.stop()
        return store.stats()["events"]

    assert asyncio.run(go()) == 10


def test_the_socket_is_private_to_its_group_and_a_stale_one_is_replaced(run_dir):
    async def go():
        (run_dir / "events.sock").write_text("left over from a crash")
        async with Running(run_dir) as r:
            mode = stat.S_IMODE(os.stat(r.events).st_mode)
            send(r.events, make_event(0))
            await until(lambda: r.store.stats()["events"] == 1)
        return mode, (run_dir / "events.sock").exists()

    mode, left = asyncio.run(go())
    assert mode == 0o660 and left is False  # and it cleans up after itself


def test_housekeeping_applies_the_retention_without_being_asked(run_dir):
    async def go():
        clock = [1_800_000_000.0]
        store = EventStore(None, routine_days=1, clock=lambda: clock[0])
        old = LogEvent(time=datetime.fromtimestamp(clock[0] - 3 * 86400, timezone.utc), boot_id=BOOT, seq=0, severity=Severity.INFO,
                       event="test.old", module="core", module_version="1", message="old")
        store.insert_many([old])
        async with Running(run_dir, store=store, prune_seconds=0.05):
            await until(lambda: store.stats()["events"] == 0)

    asyncio.run(go())


# -- searches out -----------------------------------------------------------------------------------------------


def test_the_query_socket_filters_pages_and_searches(run_dir):
    async def go():
        async with Running(run_dir) as r:
            for i in range(30):
                send(r.events, make_event(i, message=f"line {i}", severity=Severity.ERROR if i % 10 == 0 else Severity.INFO,
                                           module="api" if i % 2 else "core"))
            await until(lambda: r.store.stats()["events"] == 30)
            errors = await get(r.query, "/events?min_severity=error&limit=50")
            by_name = await get(r.query, "/events?min_severity=40")
            api = await get(r.query, "/events?module=api&limit=5")
            more = await get(r.query, f"/events?module=api&limit=5&before={api[1]['next_before']}")
            found = await get(r.query, "/events?q=line+7")
            return errors, by_name, api, more, found

    errors, by_name, api, more, found = asyncio.run(go())
    assert sorted(e["message"] for e in errors[1]["events"]) == ["line 0", "line 10", "line 20"] == sorted(
        e["message"] for e in by_name[1]["events"]
    )  # a severity by name or by number
    assert len(api[1]["events"]) == 5 and api[1]["next_before"] is not None
    assert not {e["id"] for e in api[1]["events"]} & {e["id"] for e in more[1]["events"]}
    assert [e["message"] for e in found[1]["events"]] == ["line 7"]


def test_times_can_be_given_as_seconds_or_as_iso_8601(run_dir):
    async def go():
        async with Running(run_dir) as r:
            send(r.events, make_event(0, message="now"))
            await until(lambda: r.store.stats()["events"] == 1)
            now = time.time()
            by_seconds = await get(r.query, f"/events?since={now - 60}&until={now + 60}")
            by_iso = await get(r.query, "/events?since=2000-01-01T00:00:00Z")
            future = await get(r.query, f"/events?since={now + 3600}")
            return by_seconds, by_iso, future

    by_seconds, by_iso, future = asyncio.run(go())
    assert len(by_seconds[1]["events"]) == 1 and len(by_iso[1]["events"]) == 1 and future[1]["events"] == []


def test_stats_gaps_and_health(run_dir):
    async def go():
        async with Running(run_dir) as r:
            send(r.events, make_event(0))
            send(r.events, make_event(5))  # four missing
            await until(lambda: r.store.stats()["events"] == 2)
            return await get(r.query, "/stats"), await get(r.query, "/gaps"), await get(r.query, "/health")

    stats, gaps, health = asyncio.run(go())
    assert stats[1]["events"] == 2 and stats[1]["collector"]["received"] == 2 and "core" in stats[1]["modules"]
    assert gaps[1]["gaps"][0]["kind"] == "sequence_gap" and gaps[1]["gaps"][0]["count"] == 4
    assert health[0] == 200 and health[1]["state"] == "ok"


@pytest.mark.parametrize("target", [
    "/events?limit=abc", "/events?before=x", "/events?since=yesterday", "/events?min_severity=loud",
    "/events?q=" + "a" * 300, "/events?module=" + "m" * 90, "/gaps?limit=-", "/events?until=nan", "/events?since=inf",
])
def test_bad_queries_are_refused_and_the_collector_carries_on(run_dir, target):
    async def go():
        async with Running(run_dir) as r:
            return await get(r.query, target), await get(r.query, "/health")

    bad, health = asyncio.run(go())
    assert bad[0] == 400 and "error" in bad[1] and health[0] == 200


def test_unknown_paths_and_other_methods_are_refused(run_dir):
    async def go():
        async with Running(run_dir) as r:
            missing = await get(r.query, "/nope")
            reader, writer = await asyncio.open_unix_connection(str(r.query))
            writer.write(b"POST /events HTTP/1.1\r\nHost: logd\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
            data = await asyncio.wait_for(reader.read(), 5)
            writer.close()
            return missing, data

    missing, post = asyncio.run(go())
    assert missing[0] == 404 and post.startswith(b"HTTP/1.1 405") and b"GET" in post


def test_a_huge_request_is_refused_without_reading_it_all(run_dir):
    async def go():
        async with Running(run_dir) as r:
            reader, writer = await asyncio.open_unix_connection(str(r.query))
            writer.write(b"GET /events?q=" + b"a" * 20_000 + b" HTTP/1.1\r\nHost: logd\r\n\r\n")
            await writer.drain()
            data = await asyncio.wait_for(reader.read(), 5)
            writer.close()
            return data, await get(r.query, "/health")

    data, health = asyncio.run(go())
    assert data.startswith(b"HTTP/1.1 400") and health[0] == 200


def test_a_client_that_says_nothing_is_let_go(run_dir, monkeypatch):
    from logd import server

    monkeypatch.setattr(server, "REQUEST_DEADLINE_SECONDS", 0.3)

    async def go():
        async with Running(run_dir) as r:
            reader, writer = await asyncio.open_unix_connection(str(r.query))
            data = await asyncio.wait_for(reader.read(), 5)  # sends nothing, so the server answers and hangs up
            writer.close()
            return data, await get(r.query, "/health")

    data, health = asyncio.run(go())
    assert data.startswith(b"HTTP/1.1 408") and health[0] == 200


def test_a_query_that_fails_inside_the_store_is_an_error_answer_not_a_dead_server(run_dir):
    async def go():
        async with Running(run_dir) as r:
            def broken(**kwargs):
                raise RuntimeError("database is locked")

            r.store.query = broken
            return await get(r.query, "/events"), await get(r.query, "/health")

    failed, health = asyncio.run(go())
    assert failed[0] == 503 and health[0] == 200


def test_no_secret_a_sender_was_given_is_in_what_comes_back(run_dir):
    async def go():
        async with Running(run_dir) as r:
            log = logging.getLogger("moreopenrepeater.test_logd_secrets")
            log.setLevel(logging.DEBUG)
            log.propagate = False
            handler = CollectorHandler(ProcessIdentity("api", "0.1.0"), r.events)
            log.addHandler(handler)
            try:
                log_event(log, Severity.INFO, "auth.login", user="alice", password="hunter2", token="t0k3n")
                await until(lambda: r.store.stats()["events"] == 1)
                return await get(r.query, "/events?q=hunter2"), await get(r.query, "/events?event=auth.login")
            finally:
                log.removeHandler(handler)
                handler.close()

    searched, listed = asyncio.run(go())
    assert searched[1]["events"] == [] and "hunter2" not in json.dumps(listed[1]) and "t0k3n" not in json.dumps(listed[1])
