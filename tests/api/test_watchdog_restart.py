"""A stalled core restarts itself in a container (#221, #229). Found on the Pi on 2026-11-19: the AIOC dropped off USB for
two seconds, the audio engine stalled, and the container sat "unhealthy" for 18 minutes with PTT commands going
nowhere, because Docker does not restart an unhealthy container and there is no systemd to do it."""
import subprocess
import sys
import threading
import time

import pytest

from api.watchdog import (
    CONTAINER_CHECK_SECONDS, EXIT_STALLED, Watchdog, exit_for_restart, exit_on_stall_enabled,
)


def make_watchdog(restart=None):
    clock = {"now": 100.0}
    watchdog = Watchdog(notify=lambda message: True, clock=lambda: clock["now"], stall_seconds=10)
    watchdog.restart = restart
    return watchdog, clock


def test_a_stall_runs_the_unkey_hook_then_the_restart_once_and_never_while_progress_is_made(structured_events):
    calls = []
    watchdog, clock = make_watchdog(restart=lambda name: calls.append(("restart", name)))
    watchdog.on_stall = lambda name: calls.append(("unkey", name))
    last = {"at": 100.0}
    watchdog.watch("audio engine", lambda: last["at"])
    clock["now"] = 105.0
    assert watchdog.check() is True and calls == []  # progress is fresh: nothing happens
    clock["now"] = 111.0  # 11 s without a block
    assert watchdog.check() is False
    assert calls == [("unkey", "audio engine"), ("restart", "audio engine")]  # the transmitter comes off the air first
    watchdog.check()
    watchdog.check()
    assert calls == [("unkey", "audio engine"), ("restart", "audio engine")]  # and it is only ever asked once


def test_the_stall_is_a_named_event_with_which_part_and_a_code(structured_events):
    watchdog, clock = make_watchdog()
    watchdog.watch("audio engine", lambda: 100.0)
    clock["now"] = 120.0
    watchdog.check()
    [stalled] = structured_events.named("watchdog.stalled")
    assert stalled.severity == 40 and stalled.error_code == "watchdog.stalled"
    assert stalled.context == {"part": "audio engine", "stall_s": 10}
    assert stalled.message == "the audio engine stopped making progress; restarting the service"


def test_without_a_restart_hook_it_behaves_as_it_did_under_systemd():
    watchdog, clock = make_watchdog(restart=None)
    watchdog.watch("controller", lambda: 100.0)
    clock["now"] = 120.0
    assert watchdog.check() is False and watchdog.stalled == "controller"  # the pings stop; systemd does the rest


def test_a_part_that_is_not_running_cannot_stall():
    called = []
    watchdog, clock = make_watchdog(restart=called.append)
    watchdog.watch("link radio audio engine", lambda: None)
    clock["now"] = 10_000.0
    assert watchdog.check() is True and called == []


def test_the_exit_waits_for_the_log_to_flush_and_leaves_with_the_stalled_status():
    steps = []

    def slow_flush():
        time.sleep(0.15)  # the stall event is still on its way to the collector
        steps.append("flush")

    exit_for_restart("audio engine", flush=slow_flush, leave=lambda code: steps.append(("exit", code)))
    assert steps == ["flush", ("exit", EXIT_STALLED)] and EXIT_STALLED == 75


def test_a_log_flush_that_hangs_cannot_keep_a_stalled_process_alive():
    release = threading.Event()
    left = []
    started = time.monotonic()
    exit_for_restart("audio engine", flush=release.wait, flush_seconds=0.2, leave=left.append)
    release.set()
    assert left == [EXIT_STALLED] and time.monotonic() - started < 2


@pytest.mark.parametrize("value, expected", [("1", True), ("true", True), ("YES", True), ("on", True), ("0", False), ("", False), ("no", False)])
def test_the_switch_is_read_from_the_environment(value, expected):
    assert exit_on_stall_enabled({"MOREOPENREPEATER_EXIT_ON_STALL": value}) is expected
    assert exit_on_stall_enabled({}) is False


def test_a_real_process_whose_engine_stalls_exits_with_the_restart_status():
    """Not a mock: a process runs the watchdog on its own timer, one part stops making progress, and it must end."""
    code = (
        "import time\n"
        "from api.watchdog import Watchdog, exit_for_restart\n"
        "w = Watchdog(stall_seconds=0.3)\n"
        "w.restart = exit_for_restart\n"
        "w.watch('audio engine', lambda: time.monotonic() - 5)\n"
        "w.start(0.05)\n"
        "time.sleep(30)\n"
        "raise SystemExit(0)\n"
    )
    started = time.monotonic()
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, timeout=20, text=True)
    assert result.returncode == EXIT_STALLED, result.stderr
    assert time.monotonic() - started < 10  # it did not wait out the sleep


def test_the_check_interval_for_a_container_is_short_enough_to_beat_the_health_check():
    assert 0 < CONTAINER_CHECK_SECONDS <= 5  # the stall is 10 s; the health check runs every 30
