"""The handlers that send structured events out (#221, #229)."""
import io
import json
import logging
import shutil
import socket
import tempfile
import threading
import time
from pathlib import Path

import pytest

from contracts import LogEvent, Severity
from observability.events import ProcessIdentity, log_event
from observability.handlers import CollectorHandler, JsonLineHandler


@pytest.fixture
def sock_dir():
    path = Path(tempfile.mkdtemp(prefix="mor-", dir="/tmp"))  # AF_UNIX paths are short
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def logger():
    log = logging.getLogger("moreopenrepeater.test_handlers")
    log.setLevel(logging.DEBUG)
    log.propagate = False
    yield log
    for handler in list(log.handlers):
        log.removeHandler(handler)
        handler.close()


def listen(path, rcvbuf=None):
    receiver = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    if rcvbuf:
        receiver.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, rcvbuf)
    receiver.bind(str(path))
    receiver.settimeout(5)
    return receiver


def receive(receiver, count):
    return [LogEvent.model_validate_json(receiver.recv(70_000)) for _ in range(count)]


def wait_for(condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.01)


# -- stdout ---------------------------------------------------------------------------------------


def test_each_record_is_one_json_line_a_collector_can_read(logger):
    out = io.StringIO()
    logger.addHandler(JsonLineHandler(ProcessIdentity("api", "0.1.0"), out))
    logger.info("an old style line")
    log_event(logger, Severity.WARNING, "audio.glitch", count=2)
    lines = out.getvalue().splitlines()
    events = [LogEvent.model_validate_json(line) for line in lines]
    assert [e.event for e in events] == ["legacy.test_handlers", "audio.glitch"]
    assert [e.seq for e in events] == [0, 1] and events[0].boot_id == events[1].boot_id
    assert events[1].context == {"count": 2} and all(e.module == "api" for e in events)


def test_a_stream_that_fails_does_not_raise_into_the_caller(logger):
    class Broken(io.StringIO):
        def write(self, text):
            raise OSError("pipe closed")

    handler = JsonLineHandler(ProcessIdentity("core", "0.1.0"), Broken())
    handler.handleError = lambda record: None  # logging's own policy for a handler error
    logger.addHandler(handler)
    logger.info("still fine")


# -- the collector --------------------------------------------------------------------------------


def make_handler(path, **kwargs):
    return CollectorHandler(ProcessIdentity("core", "0.1.0"), path, **kwargs)


def test_events_reach_the_collector_in_order(sock_dir, logger):
    receiver = listen(sock_dir / "logd.sock")
    handler = make_handler(sock_dir / "logd.sock")
    logger.addHandler(handler)
    for number in range(5):
        log_event(logger, Severity.INFO, "test.step", number=number)
    events = receive(receiver, 5)
    assert [e.context["number"] for e in events] == [0, 1, 2, 3, 4]
    assert [e.seq for e in events] == sorted(e.seq for e in events)
    assert handler.dropped_total == 0


def test_with_no_collector_logging_is_instant_and_the_loss_is_counted(sock_dir, logger):
    handler = make_handler(sock_dir / "nothing.sock")
    logger.addHandler(handler)
    started = time.perf_counter()
    for _ in range(200):
        logger.info("nobody is listening")
    assert (time.perf_counter() - started) / 200 < 0.005  # microseconds in practice; never a wait
    wait_for(lambda: handler.dropped_total == 200)


def test_once_the_collector_is_back_it_is_told_how_many_were_lost(sock_dir, logger):
    handler = make_handler(sock_dir / "logd.sock")
    logger.addHandler(handler)
    for _ in range(7):
        logger.info("lost")
    wait_for(lambda: handler.dropped_total == 7)
    receiver = listen(sock_dir / "logd.sock")
    logger.info("the first one it hears")
    events = receive(receiver, 2)
    assert events[0].event == "legacy.test_handlers"
    gap = events[1]
    assert gap.event == "logging.dropped" and gap.severity is Severity.WARNING
    assert gap.context["dropped"] == 7 and handler.dropped_total == 7


def test_a_collector_that_stops_reading_never_holds_up_the_caller(sock_dir, logger):
    receiver = listen(sock_dir / "logd.sock", rcvbuf=1024)  # tiny, and never read
    handler = make_handler(sock_dir / "logd.sock")
    logger.addHandler(handler)
    started = time.perf_counter()
    for number in range(1500):
        log_event(logger, Severity.INFO, "test.flood", payload="x" * 400, number=number)
    assert (time.perf_counter() - started) / 1500 < 0.005
    wait_for(lambda: handler.dropped_total > 0)
    receiver.close()


def test_an_event_too_big_for_a_datagram_is_dropped_and_counted(sock_dir, logger):
    receiver = listen(sock_dir / "logd.sock")
    handler = make_handler(sock_dir / "logd.sock", max_datagram=400)
    logger.addHandler(handler)
    logger.info("small")
    log_event(logger, Severity.INFO, "test.big", a="y" * 450, b="z" * 450)
    assert receive(receiver, 1)[0].message == "small"
    wait_for(lambda: handler.dropped_total == 1)


def test_a_full_queue_drops_new_events_instead_of_waiting(sock_dir, logger):
    handler = make_handler(sock_dir / "logd.sock", queue_size=5)
    release = threading.Event()
    real = handler._send
    handler._send = lambda data: (release.wait(5), real(data))[1]  # the sender is stuck
    logger.addHandler(handler)
    started = time.perf_counter()
    for _ in range(100):
        logger.info("backlog")
    assert (time.perf_counter() - started) < 0.5
    assert handler.dropped_total >= 90  # the queue held about five and one was in the sender's hand
    release.set()


def test_closing_delivers_what_is_queued_and_stops_the_thread(sock_dir, logger):
    receiver = listen(sock_dir / "logd.sock")
    handler = make_handler(sock_dir / "logd.sock")
    logger.addHandler(handler)
    for number in range(3):
        log_event(logger, Severity.INFO, "test.last", number=number)
    handler.close()
    assert [e.context["number"] for e in receive(receiver, 3)] == [0, 1, 2]
    assert not handler.running
    logger.info("after close")  # nothing raises, nothing is sent
    receiver.settimeout(0.2)
    with pytest.raises(socket.timeout):
        receiver.recv(70_000)


def test_the_caller_pays_for_a_snapshot_not_for_the_event(sock_dir, logger):
    """Building the event (validation, redaction) happens on the sender's thread, not the caller's."""
    handler = make_handler(sock_dir / "logd.sock")
    seen_threads = []
    real = handler._build

    def spy(*args):
        seen_threads.append(threading.current_thread().name)
        return real(*args)

    handler._build = spy
    logger.addHandler(handler)
    logger.info("work done elsewhere")
    wait_for(lambda: seen_threads)
    assert seen_threads == ["log-collector-sender"]


def test_events_are_json_with_no_secrets_that_were_given_as_context(sock_dir, logger):
    receiver = listen(sock_dir / "logd.sock")
    logger.addHandler(make_handler(sock_dir / "logd.sock"))
    log_event(logger, Severity.INFO, "auth.login", user="alice", password="hunter2", token="t0k3n")
    data = receiver.recv(70_000)
    assert b"hunter2" not in data and b"t0k3n" not in data
    assert json.loads(data)["context"]["user"] == "alice"
