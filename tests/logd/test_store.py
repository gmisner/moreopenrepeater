"""The collector's event store (#221, #229): a bounded, searchable SQLite file."""
import itertools
import threading
import time
from datetime import datetime, timezone

import pytest

from contracts import LogEvent, Severity
from logd.store import EventStore

BOOT = "b0000000000000000000000000000001"
NOW = 1_800_000_000.0
_seq = itertools.count()


def event(*, seq=None, severity=Severity.INFO, name="test.event", module="core", boot=BOOT, message="a thing happened",
          when=NOW, correlation=None, error_code=None, **context):
    return LogEvent(
        time=datetime.fromtimestamp(when, timezone.utc), boot_id=boot, seq=next(_seq) if seq is None else seq,
        severity=severity, event=name, module=module, module_version="0.1.0", message=message,
        correlation_id=correlation, error_code=error_code, context=context,
    )


@pytest.fixture
def store():
    clock = [NOW]
    s = EventStore(None, clock=lambda: clock[0])
    s.now = clock  # tests move time by setting now[0]
    yield s
    s.close()


def texts(result):
    return [e["message"] for e in result["events"]]


# -- storing and reading back -----------------------------------------------------------------------


def test_what_is_stored_comes_back_newest_first_with_every_field(store):
    store.insert_many([
        event(message="first", when=NOW - 10),
        event(message="second", when=NOW - 5, severity=Severity.ERROR, name="audio.glitch", correlation="tx-0123456789ab",
              error_code="audio.overrun", count=2, state="idle"),
    ])
    result = store.query()
    assert texts(result) == ["second", "first"]
    row = result["events"][0]
    assert row["severity"] == 40 and row["event"] == "audio.glitch" and row["module"] == "core"
    assert row["correlation_id"] == "tx-0123456789ab" and row["error_code"] == "audio.overrun"
    assert row["context"] == {"count": 2, "state": "idle"} and row["boot_id"] == BOOT
    assert row["time"].startswith(datetime.fromtimestamp(NOW - 5, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"))
    assert result["next_before"] is None


def test_an_event_with_no_message_or_context_is_fine(store):
    store.insert_many([LogEvent(time=datetime.fromtimestamp(NOW, timezone.utc), boot_id=BOOT, seq=0,
                                severity=Severity.INFO, event="x.y", module="core", module_version="1")])
    row = store.query()["events"][0]
    assert row["message"] is None and row["context"] == {}


def test_filters(store):
    store.insert_many([
        event(message="a", when=NOW - 50, module="core"),
        event(message="b", when=NOW - 40, module="api", severity=Severity.WARNING, name="weather.poll_failed"),
        event(message="c", when=NOW - 30, module="core", severity=Severity.ERROR, name="audio.glitch", correlation="tx-aaaaaaaa1"),
        event(message="d", when=NOW - 20, module="core", severity=Severity.INFO, name="audio.engine_started"),
        event(message="e", when=NOW - 10, module="api", severity=Severity.CRITICAL, name="auth.locked"),
    ])
    assert sorted(texts(store.query(min_severity=40))) == ["c", "e"]
    assert sorted(texts(store.query(module="api"))) == ["b", "e"]
    assert texts(store.query(event="audio.glitch")) == ["c"]
    assert sorted(texts(store.query(event="audio.*"))) == ["c", "d"]  # a prefix, not a wildcard anywhere else
    assert texts(store.query(correlation_id="tx-aaaaaaaa1")) == ["c"]
    assert sorted(texts(store.query(since=NOW - 35, until=NOW - 15))) == ["c", "d"]
    assert texts(store.query(module="core", min_severity=30, since=NOW - 45)) == ["c"]


def test_a_like_pattern_in_a_filter_is_not_a_pattern(store):
    store.insert_many([event(message="x", name="audio.glitch"), event(message="y", name="audiox.glitch")])
    assert texts(store.query(event="audio%")) == []  # % and _ are plain characters here
    assert texts(store.query(event="audio_.*")) == []
    assert texts(store.query(event="audio.*")) == ["x"]  # and a prefix means that prefix, not anything like it


def test_paging_walks_back_through_everything_exactly_once(store):
    store.insert_many([event(message=str(i), when=NOW - 100 + i) for i in range(25)])
    seen, cursor = [], None
    while True:
        page = store.query(limit=10, before=cursor)
        seen += texts(page)
        cursor = page["next_before"]
        if cursor is None:
            break
    assert seen == [str(i) for i in range(24, -1, -1)]


def test_a_page_never_exceeds_the_maximum(store):
    store.insert_many([event() for _ in range(30)])
    assert len(store.query(limit=10_000, max_limit=20)["events"]) == 20


# -- search ---------------------------------------------------------------------------------------


def test_text_search_finds_words_in_the_message_the_name_and_the_context(store):
    store.insert_many([
        event(message="audio engine started", name="audio.engine_started"),
        event(message="nothing relevant", name="net.checkin", callsign="W1AW"),
        event(message="link dropped", name="link.disconnect", node="41520"),
    ])
    assert texts(store.query(q="engine")) == ["audio engine started"]
    assert texts(store.query(q="W1AW")) == ["nothing relevant"]  # a context value
    assert texts(store.query(q="link.disconnect")) == ["link dropped"]  # the event name
    assert texts(store.query(q="ENGINE Started")) == ["audio engine started"]  # any case, every word
    assert texts(store.query(q="engine nonexistent")) == []


@pytest.mark.parametrize("q", ['"', '"unbalanced', "a AND", "OR OR", "NOT", "*", "a*", "(x", "x)", "NEAR(a b)", "col:val", "-x", "^y", "'"])
def test_search_syntax_typed_by_a_person_is_text_not_an_error(store, q):
    store.insert_many([event(message="a plain line")])
    store.query(q=q)  # must not raise; the answer may well be empty


def test_search_combines_with_the_other_filters(store):
    store.insert_many([
        event(message="glitch one", module="core", severity=Severity.WARNING),
        event(message="glitch two", module="api", severity=Severity.WARNING),
        event(message="glitch three", module="core", severity=Severity.INFO),
    ])
    assert texts(store.query(q="glitch", module="core", min_severity=30)) == ["glitch one"]


# -- retention and the quota ---------------------------------------------------------------------


def test_routine_events_are_kept_14_days_and_errors_30(store):
    day = 86400
    store.insert_many([
        event(message="routine old", when=NOW - 15 * day),
        event(message="routine ok", when=NOW - 13 * day),
        event(message="error old", when=NOW - 31 * day, severity=Severity.ERROR),
        event(message="error ok", when=NOW - 29 * day, severity=Severity.ERROR),
        event(message="critical ok", when=NOW - 20 * day, severity=Severity.CRITICAL),
    ])
    removed = store.prune()
    assert removed["retention"] == 2
    assert sorted(texts(store.query())) == ["critical ok", "error ok", "routine ok"]


def test_the_retention_can_be_set(store):
    day = 86400
    s = EventStore(None, routine_days=2, critical_days=5, clock=lambda: NOW)
    s.insert_many([event(message="a", when=NOW - 3 * day), event(message="b", when=NOW - 6 * day, severity=Severity.ERROR)])
    s.prune()
    assert s.query()["events"] == []
    s.close()


def test_a_deleted_event_is_no_longer_found_by_search(store):
    store.insert_many([event(message="remember the milk", when=NOW - 20 * 86400)])
    assert texts(store.query(q="milk")) == ["remember the milk"]
    store.prune()
    assert store.query(q="milk")["events"] == []


def test_the_quota_removes_routine_events_first_and_oldest_first(tmp_path):
    s = EventStore(tmp_path / "events.db", max_bytes=300_000, clock=lambda: NOW)
    errors = [event(message=f"error {i}", severity=Severity.ERROR, when=NOW - 5000 + i) for i in range(20)]
    routine = [event(message=f"routine {i}", blob="x" * 300, when=NOW - 4000 + i) for i in range(2500)]
    s.insert_many(errors + routine)
    assert s.used_bytes() > 300_000
    removed = s.prune()
    assert removed["quota"] > 0 and s.used_bytes() <= 300_000
    kept = texts(s.query(limit=1000, max_limit=1000))
    assert all(f"error {i}" in kept for i in range(20))  # the errors outlived the routine events older than them
    routine_kept = sorted(int(t.split()[1]) for t in texts(s.query(limit=3000, max_limit=3000)) if t.startswith("routine"))
    assert routine_kept and routine_kept[-1] == 2499 and routine_kept[0] > 0  # the oldest went, the newest stayed
    assert any(g["kind"] == "quota_pruned" and g["count"] == removed["quota"] for g in s.gaps())
    s.close()


def test_when_only_errors_are_left_and_it_is_still_over_the_quota_the_oldest_errors_go(tmp_path):
    s = EventStore(tmp_path / "events.db", max_bytes=300_000, clock=lambda: NOW)
    s.insert_many([event(message=f"error {i}", blob="y" * 300, severity=Severity.ERROR, when=NOW - 3000 + i) for i in range(2500)])
    s.prune()
    assert s.used_bytes() <= 300_000
    kept = [int(t.split()[1]) for t in texts(s.query(limit=3000, max_limit=3000))]
    assert kept and max(kept) == 2499 and min(kept) > 0
    s.close()


def test_the_file_shrinks_after_a_prune(tmp_path):
    s = EventStore(tmp_path / "events.db", max_bytes=300_000, clock=lambda: NOW)
    s.insert_many([event(message="z", blob="z" * 300, when=NOW - 100 + (i % 50)) for i in range(3000)])
    s._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    before = (tmp_path / "events.db").stat().st_size
    assert before > 600_000
    s.prune()
    s._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    assert (tmp_path / "events.db").stat().st_size < before / 2
    s.close()


def test_many_prunes_that_each_remove_a_little_shrink_the_file_at_most_once_an_hour(tmp_path):
    """After 14 days of uptime routine events expire every minute. Compacting and checkpointing each time is
    SD card wear for nothing, so it waits an hour."""
    clock = [NOW]
    s = EventStore(tmp_path / "events.db", clock=lambda: clock[0])
    # One event a minute for 3 hours, all of it routine and old enough to expire as the clock moves.
    s.insert_many([event(message=f"m{i}", when=NOW + i * 60) for i in range(180)])
    clock[0] = NOW + 14 * 86400
    prunes_that_removed_something = 0
    for minute in range(180):
        clock[0] = NOW + 14 * 86400 + minute * 60
        if s.prune()["retention"]:
            prunes_that_removed_something += 1
    assert prunes_that_removed_something >= 150
    assert 1 <= s.shrinks <= 4  # the first prune, then once per hour: 3 hours is at most 4, never 150
    s.close()


def test_the_first_prune_that_removes_something_still_shrinks_and_the_next_one_waits(tmp_path):
    clock = [NOW]
    s = EventStore(tmp_path / "events.db", clock=lambda: clock[0])
    s.insert_many([event(message=f"m{i}", when=NOW - 15 * 86400 + i) for i in range(10)])
    s.insert_many([event(message=f"n{i}", when=NOW - 14 * 86400 + 30 + i) for i in range(10)])
    assert s.prune()["retention"] == 10 and s.shrinks == 1
    clock[0] = NOW + 600  # ten minutes later more has expired
    assert s.prune()["retention"] == 10 and s.shrinks == 1  # nothing extra was written to the card
    clock[0] = NOW + 3600
    s.insert_many([event(message="late", when=NOW - 15 * 86400)])
    assert s.prune()["retention"] == 1 and s.shrinks == 2  # an hour on, it is due again
    s.close()


def test_the_size_cap_still_holds_when_the_shrink_is_not_due(tmp_path):
    s = EventStore(tmp_path / "events.db", max_bytes=300_000, clock=lambda: NOW, shrink_interval_seconds=10**9)
    s.insert_many([event(message="old", when=NOW - 15 * 86400)])
    s.prune()  # uses up the one shrink this store will do
    assert s.shrinks == 1
    s.insert_many([event(message=f"routine {i}", blob="x" * 300, when=NOW - 4000 + i) for i in range(5000)])
    assert s.used_bytes() > 300_000
    removed = s.prune()
    assert removed["quota"] > 0 and s.used_bytes() <= 300_000 and s.shrinks == 1
    # The quota loop gave the freed pages back itself, so the file is back under the cap too, apart from the log.
    s._conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
    assert (tmp_path / "events.db").stat().st_size <= 300_000 + 64 * 1024
    s.close()


def test_a_quota_below_what_an_empty_file_needs_empties_it_and_stops(tmp_path):
    s = EventStore(tmp_path / "events.db", max_bytes=1000, clock=lambda: NOW)
    s.insert_many([event(message="a"), event(message="b")])
    s.prune()  # must terminate
    assert s.query()["events"] == []
    s.close()


def test_a_prune_that_is_not_needed_removes_nothing(tmp_path):
    s = EventStore(tmp_path / "events.db", clock=lambda: NOW)
    s.insert_many([event(message="a")])
    assert s.prune() == {"retention": 0, "quota": 0} and texts(s.query()) == ["a"]
    s.close()


# -- accounting for what is missing -----------------------------------------------------------------------


def test_a_skipped_sequence_number_is_recorded_as_a_gap_with_how_many(store):
    store.insert_many([event(seq=0), event(seq=1), event(seq=5), event(seq=6)])
    gaps = [g for g in store.gaps() if g["kind"] == "sequence_gap"]
    assert len(gaps) == 1 and gaps[0]["count"] == 3 and gaps[0]["module"] == "core" and gaps[0]["boot_id"] == BOOT


def test_a_new_boot_id_is_a_restart_not_a_gap(store):
    store.insert_many([event(seq=0), event(seq=1), event(seq=0, boot="b0000000000000000000000000000002")])
    kinds = [g["kind"] for g in store.gaps()]
    assert kinds == ["restart"] and "sequence_gap" not in kinds


def test_modules_are_counted_separately(store):
    store.insert_many([event(seq=0, module="core"), event(seq=0, module="api", boot="b0000000000000000000000000000002"),
                       event(seq=1, module="core"), event(seq=1, module="api", boot="b0000000000000000000000000000002")])
    assert [g for g in store.gaps() if g["kind"] == "sequence_gap"] == []


def test_a_repeated_or_late_sequence_number_is_ignored_not_counted_as_a_gap(store):
    store.insert_many([event(seq=0), event(seq=1), event(seq=1), event(seq=0), event(seq=2)])
    assert [g for g in store.gaps() if g["kind"] == "sequence_gap"] == []


def test_the_senders_own_report_of_drops_is_recorded_with_its_count(store):
    store.insert_many([event(seq=0), event(seq=1, name="logging.dropped", severity=Severity.WARNING, dropped=7,
                                           since="2026-11-18T00:00:00+00:00")])
    reported = [g for g in store.gaps() if g["kind"] == "reported_drop"]
    assert len(reported) == 1 and reported[0]["count"] == 7 and reported[0]["module"] == "core"
    assert store.query(event="logging.dropped")["events"]  # and it is still an event you can find


def test_a_collector_that_was_down_reports_what_it_missed_when_it_comes_back(tmp_path):
    path = tmp_path / "events.db"
    first = EventStore(path, clock=lambda: NOW)
    first.insert_many([event(seq=i) for i in range(5)])
    first.close()
    second = EventStore(path, clock=lambda: NOW)  # logd restarted; the sender carried on counting
    second.insert_many([event(seq=9)])
    gap = [g for g in second.gaps() if g["kind"] == "sequence_gap"]
    assert len(gap) == 1 and gap[0]["count"] == 4
    second.close()


# -- the rest ---------------------------------------------------------------------------------------------


def test_stats_say_what_is_held_and_where_each_module_is_up_to(store):
    store.insert_many([event(seq=0, when=NOW - 100), event(seq=1, when=NOW - 10, severity=Severity.ERROR)])
    stats = store.stats()
    assert stats["events"] == 2 and stats["errors"] == 1
    assert stats["oldest"].startswith(datetime.fromtimestamp(NOW - 100, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"))
    assert stats["quota_bytes"] > 0 and stats["used_bytes"] > 0
    assert stats["modules"]["core"]["boot_id"] == BOOT and stats["modules"]["core"]["last_seq"] == 1
    assert stats["retention"] == {"routine_days": 14, "critical_days": 30}


def test_stats_of_an_empty_store(store):
    stats = store.stats()
    assert stats["events"] == 0 and stats["oldest"] is None and stats["modules"] == {}


def test_a_file_store_keeps_what_it_was_given_across_a_restart(tmp_path):
    first = EventStore(tmp_path / "events.db", clock=lambda: NOW)
    first.insert_many([event(message="kept")])
    first.close()
    second = EventStore(tmp_path / "events.db", clock=lambda: NOW)
    assert texts(second.query()) == ["kept"]
    second.close()


def test_inserting_in_one_thread_while_another_reads_does_not_fail(tmp_path):
    s = EventStore(tmp_path / "events.db", clock=lambda: NOW)
    stop = threading.Event()
    errors = []

    def read():
        while not stop.is_set():
            try:
                s.query(q="busy")
                s.stats()
            except Exception as error:  # noqa: BLE001
                errors.append(error)

    reader = threading.Thread(target=read)
    reader.start()
    for _ in range(40):
        s.insert_many([event(message="busy line") for _ in range(50)])
    stop.set()
    reader.join(5)
    assert errors == [] and s.stats()["events"] == 2000
    s.close()


def test_a_batch_of_thousands_goes_in_quickly_in_one_transaction(tmp_path):
    s = EventStore(tmp_path / "events.db", clock=lambda: NOW)
    batch = [event(message=f"line {i}", count=i) for i in range(5000)]
    started = time.perf_counter()
    s.insert_many(batch)
    assert time.perf_counter() - started < 3.0
    s.close()


def test_an_insert_that_fails_halfway_leaves_nothing_half_written_and_no_false_gap(store):
    store.insert_many([event(seq=i, message="before") for i in range(5)])
    real = store._insert_rows

    def half(rows):
        real(rows[:1])  # one row goes in, then the disk fills
        raise RuntimeError("disk full")

    store._insert_rows = half
    with pytest.raises(RuntimeError):
        store.insert_many([event(seq=5, message="lost 1"), event(seq=6, message="lost 2")])
    store._insert_rows = real
    assert texts(store.query()) == ["before"] * 5 and store.query(q="lost")["events"] == []
    store.insert_many([event(seq=5, message="retried 1"), event(seq=6, message="retried 2")])
    assert [g for g in store.gaps() if g["kind"] == "sequence_gap"] == []  # the failed batch did not move the count
