import json
import os
import socket
import tempfile
import threading

from contracts import DeliveryError, Emitter, ListSink, Metrics, Severity, UnixDatagramSink


def make(sink=None, **kwargs):
    sink = sink or ListSink()
    return Emitter("core", "0.1.0", [sink], **kwargs), sink


def lines(sink):
    return [json.loads(line) for line in sink.lines]


def test_events_reach_the_sink_in_emit_order_with_increasing_seq():
    emitter, sink = make()
    for i in range(5):
        emitter.emit(Severity.INFO, "tx.started", index=i)
    assert emitter.drain_once() == 5
    out = lines(sink)
    assert [e["seq"] for e in out] == [0, 1, 2, 3, 4]
    assert [e["context"]["index"] for e in out] == [0, 1, 2, 3, 4]
    assert {e["boot_id"] for e in out} == {emitter.sequencer.boot_id}


def test_redaction_happens_off_the_hot_path_but_before_any_sink():
    emitter, sink = make()
    emitter.emit(Severity.INFO, "auth.attempt", password="hunter2", latitude=33.448)
    emitter.drain_once()
    assert "hunter2" not in sink.lines[0]
    assert lines(sink)[0]["context"] == {"password": "[redacted]", "latitude": 33.4}


def test_emit_never_raises_even_for_bad_input():
    emitter, sink = make()
    emitter.emit(Severity.INFO, "not a valid name")  # fails validation on the drain thread, not here
    emitter.emit(Severity.INFO, "tx.started", obj=object())
    emitter.drain_once()
    assert emitter.metrics.get("mor_log_events_dropped_total", reason="invalid", severity="INFO") == 1
    assert len(sink.lines) == 1  # the bad event did not take the good one with it


def test_below_level_goes_to_the_ring_only_and_level_can_change_at_runtime():
    emitter, sink = make(level=Severity.INFO)
    emitter.emit(Severity.DEBUG, "audio.detail", n=1)
    emitter.drain_once()
    assert sink.lines == []
    assert [e.event for e in emitter.dump_ring()] == ["audio.detail"]
    emitter.set_level(Severity.DEBUG)
    emitter.emit(Severity.DEBUG, "audio.detail", n=2)
    emitter.drain_once()
    assert len(sink.lines) == 1


def test_ring_is_bounded():
    emitter, _ = make(ring_size=3)
    for i in range(10):
        emitter.emit(Severity.DEBUG, "audio.detail", n=i)
    assert [e.context["n"] for e in emitter.dump_ring()] == [7, 8, 9]


def test_full_queue_drops_routine_events_and_counts_them():
    emitter, sink = make(capacity=3)
    for i in range(10):
        emitter.emit(Severity.INFO, "tx.started", n=i)
    assert emitter.metrics.get("mor_log_events_dropped_total", reason="queue_full", severity="INFO") == 7
    emitter.drain_once()
    assert [e["context"]["n"] for e in lines(sink) if e["event"] == "tx.started"] == [0, 1, 2]
    assert emitter.metrics.get("mor_log_events_dropped_total", reason="queue_full", severity="INFO") == 7  # reporting cost nothing


def test_a_warning_survives_a_flood_by_evicting_the_oldest_routine_event():
    emitter, sink = make(capacity=3)
    for i in range(3):
        emitter.emit(Severity.INFO, "tx.started", n=i)
    emitter.emit(Severity.ERROR, "ptt.fault", error_code="ptt.stuck")
    emitter.drain_once()
    events = [(e["event"], e.get("context", {}).get("n")) for e in lines(sink)]
    assert ("ptt.fault", None) in events and ("tx.started", 0) not in events


def test_a_queue_full_of_warnings_drops_the_newest_and_never_blocks():
    emitter, _ = make(capacity=2)
    for _ in range(5):
        emitter.emit(Severity.ERROR, "ptt.fault")
    assert emitter.metrics.get("mor_log_events_dropped_total", reason="queue_full", severity="ERROR") == 3


def test_loss_is_reported_to_the_collector_as_an_event():
    emitter, sink = make(capacity=2)
    for _ in range(6):
        emitter.emit(Severity.INFO, "tx.started")
    emitter.drain_once()
    report = [e for e in lines(sink) if e["event"] == "log.dropped"]
    assert report and report[0]["context"]["dropped"] == {"queue_full:INFO": 4}


class BlockedSink:
    name = "blocked"

    def __init__(self):
        self.release = threading.Event()
        self.entered = threading.Event()

    def write(self, lines):
        self.entered.set()
        self.release.wait(10)


def test_a_stuck_sink_never_blocks_emit():
    sink = BlockedSink()
    emitter = Emitter("core", "0.1.0", [sink], capacity=100, drain_interval_s=0.01)
    emitter.start()
    emitter.emit(Severity.INFO, "tx.started")
    assert sink.entered.wait(5)  # the drain thread is now stuck inside write()
    for _ in range(50_000):
        emitter.emit(Severity.INFO, "tx.started")  # returns every time; the rest are dropped
    assert emitter.metrics.get("mor_log_events_dropped_total", reason="queue_full", severity="INFO") > 49_000
    sink.release.set()
    emitter.stop()


def test_a_failing_sink_is_counted_and_does_not_stop_the_others():
    class Broken:
        name = "broken"

        def write(self, lines):
            raise OSError("disk full")

    good = ListSink()
    emitter = Emitter("core", "0.1.0", [Broken(), good])
    emitter.emit(Severity.INFO, "tx.started")
    emitter.drain_once()
    assert len(good.lines) == 1
    assert emitter.metrics.get("mor_log_sink_errors_total", sink="broken") == 1
    assert emitter.metrics.get("mor_log_events_dropped_total", reason="sink_failed", severity="ANY") == 1


def test_drain_thread_delivers_and_stop_flushes():
    emitter, sink = make(drain_interval_s=0.01)
    emitter.start()
    for i in range(20):
        emitter.emit(Severity.INFO, "tx.started", n=i)
    emitter.stop()
    assert len(sink.lines) == 20


def test_unix_datagram_sink_delivers_and_a_missing_collector_is_just_counted():
    path = os.path.join(tempfile.mkdtemp(dir="/tmp"), "logd.sock")
    server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    server.bind(path)
    server.settimeout(2)
    try:
        emitter = Emitter("core", "0.1.0", [UnixDatagramSink(path)])
        emitter.emit(Severity.INFO, "tx.started")
        emitter.drain_once()
        assert json.loads(server.recv(65536))["event"] == "tx.started"
    finally:
        server.close()
        os.unlink(path)
    emitter.emit(Severity.INFO, "tx.started")  # collector gone
    emitter.drain_once()
    assert emitter.metrics.get("mor_log_sink_errors_total", sink="logd") == 1


def test_oversize_event_is_not_sent_as_a_datagram(tmp_path):
    sink = UnixDatagramSink(str(tmp_path / "none.sock"))
    try:
        sink.write(["x" * 9000])
    except OSError as error:
        assert "1 of 1" in str(error)
    else:
        raise AssertionError("expected OSError")


def test_metrics_render_prometheus_text():
    metrics = Metrics()
    metrics.counter("mor_things_total", "Things.", ("kind",))
    metrics.gauge("mor_depth", "Depth.")
    metrics.inc("mor_things_total", kind='a"b')
    metrics.inc("mor_things_total", 2, kind='a"b')
    metrics.set("mor_depth", 4)
    text = metrics.render()
    assert '# TYPE mor_things_total counter' in text
    assert 'mor_things_total{kind="a\\"b"} 3' in text
    assert "mor_depth 4" in text


def test_metrics_reject_unregistered_labels_and_bad_names():
    import pytest

    metrics = Metrics()
    metrics.counter("mor_x_total", "X.", ("a",))
    with pytest.raises(ValueError):
        metrics.inc("mor_x_total", b="1")
    with pytest.raises(ValueError):
        metrics.counter("Bad-Name", "x")
    with pytest.raises(ValueError):
        metrics.gauge("mor_x_total", "X.")  # same name, different kind


def test_events_lost_in_a_sink_are_counted_per_event_not_per_batch():
    class PartialSink:
        name = "partial"

        def write(self, lines):
            raise DeliveryError(failed=3, total=len(lines), where="somewhere")

    emitter = Emitter("core", "0.1.0", [PartialSink()])
    for _ in range(5):
        emitter.emit(Severity.INFO, "tx.started")
    emitter.drain_once()
    assert emitter.metrics.get("mor_log_events_dropped_total", reason="sink_failed", severity="ANY") == 3
    assert emitter.metrics.get("mor_log_sink_errors_total", sink="partial") == 1


class FullThenFree:
    """A socket whose queue is full for the first `full` sends, like logd falling behind."""

    def __init__(self, full):
        self.full, self.sent = full, []

    def send(self, data):
        if self.full > 0:
            self.full -= 1
            raise BlockingIOError()
        self.sent.append(data)

    def close(self):
        pass


def test_sink_waits_for_a_slow_collector_instead_of_dropping_the_batch(monkeypatch):
    sink = UnixDatagramSink("/unused")
    sink._sock = FullThenFree(full=30)
    monkeypatch.setattr(sink, "_wait_writable", lambda timeout: True)
    sink.write(["a", "b", "c"])
    assert sink._sock.sent == [b"a", b"b", b"c"]


def test_sink_gives_up_within_its_budget_when_the_collector_never_catches_up(monkeypatch):
    sink = UnixDatagramSink("/unused", batch_budget_s=0.05)
    sink._sock = FullThenFree(full=10**9)
    monkeypatch.setattr(sink, "_wait_writable", lambda timeout: True)
    try:
        sink.write(["a", "b", "c"])
    except DeliveryError as error:
        assert error.failed == 3
    else:
        raise AssertionError("expected DeliveryError")
