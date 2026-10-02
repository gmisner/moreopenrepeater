import os
import socket
import tempfile

import pytest

from api.health import SystemProbe
from api.persistence import StateStore
from api.watchdog import Watchdog, ping_interval, sd_notify

from test_health import make_monitor, no_vcgencmd, titles, write


def test_sd_notify_sends_to_the_socket():
    path = os.path.join(tempfile.mkdtemp(dir="/tmp"), "notify")  # socket paths are short
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as server:
        server.bind(path)
        assert sd_notify("READY=1", {"NOTIFY_SOCKET": path}) is True
        assert server.recv(100) == b"READY=1"


@pytest.mark.skipif(not hasattr(socket, "AF_UNIX") or os.uname().sysname != "Linux", reason="abstract sockets are Linux-only")
def test_sd_notify_understands_abstract_sockets():
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as server:
        server.bind("\0moreopenrepeater-test")
        assert sd_notify("WATCHDOG=1", {"NOTIFY_SOCKET": "@moreopenrepeater-test"}) is True
        assert server.recv(100) == b"WATCHDOG=1"


def test_sd_notify_without_systemd(tmp_path):
    assert sd_notify("READY=1", {}) is False
    assert sd_notify("READY=1", {"NOTIFY_SOCKET": str(tmp_path / "missing")}) is False


def test_ping_interval_is_half_the_watchdog_period():
    assert ping_interval({"WATCHDOG_USEC": "30000000"}, pid=5) == 15.0
    assert ping_interval({"WATCHDOG_USEC": "30000000", "WATCHDOG_PID": "5"}, pid=5) == 15.0
    assert ping_interval({"WATCHDOG_USEC": "30000000", "WATCHDOG_PID": "6"}, pid=5) is None
    assert ping_interval({}, pid=5) is None
    assert ping_interval({"WATCHDOG_USEC": "0"}, pid=5) is None


def make_watchdog():
    sent = []
    clock = {"now": 100.0}
    watchdog = Watchdog(notify=lambda message: sent.append(message) or True, clock=lambda: clock["now"], stall_seconds=10)
    return watchdog, sent, clock


def test_pings_only_while_everything_makes_progress():
    watchdog, sent, clock = make_watchdog()
    progress = {"controller": 100.0, "audio": None}  # audio not running: can't stall
    stalls = []
    watchdog.watch("controller", lambda: progress["controller"])
    watchdog.watch("audio engine", lambda: progress["audio"])
    watchdog.on_stall = stalls.append

    clock["now"] = 105.0
    assert watchdog.check() is True
    progress["audio"] = 104.0
    clock["now"] = 112.0
    progress["controller"] = 111.0
    assert watchdog.check() is True
    assert sent == ["WATCHDOG=1", "WATCHDOG=1"]

    clock["now"] = 115.0  # audio last moved at 104
    assert watchdog.check() is False
    assert stalls == ["audio engine"] and watchdog.stalled == "audio engine"

    progress["audio"] = 115.0  # it doesn't take it back: systemd is already counting down
    assert watchdog.check() is False
    assert stalls == ["audio engine"] and len(sent) == 2


def test_a_failing_stall_hook_still_stops_the_pings():
    watchdog, sent, clock = make_watchdog()
    watchdog.watch("controller", lambda: 0.0)

    def broken(name):
        raise OSError("the CM108 is gone")

    watchdog.on_stall = broken
    assert watchdog.check() is False
    assert sent == []


def test_a_watchdog_restart_is_reported_as_one(tmp_path):
    store = StateStore(tmp_path / "run.json")
    monitor, probe, clock = make_monitor(store)
    monitor.started()
    monitor.marker.remember(watchdog="audio engine")  # what the stall hook does

    monitor, probe, clock = make_monitor(store)
    clock["now"] += 60
    probe.boot = 1_000.0
    alerts = monitor.started()

    assert titles(alerts) == ["Restarted by the watchdog"]
    assert "the audio engine stopped responding" in alerts[0].message
    assert monitor.last_unexpected_stop["watchdog"] == "audio engine"
    assert "watchdog" not in store.load()  # the next crash isn't blamed on it


def test_probe_reports_the_watchdogs(tmp_path):
    write(tmp_path, "sys/class/watchdog/watchdog0/state", "active\n")
    readings = SystemProbe(tmp_path, sys_root=tmp_path, run=no_vcgencmd, env={"WATCHDOG_USEC": "30000000"}).readings()
    assert readings["watchdog"] is True and readings["hardware_watchdog"] is True

    readings = SystemProbe(tmp_path / "none", sys_root=tmp_path / "none", run=no_vcgencmd, env={}).readings()
    assert readings["watchdog"] is False and readings["hardware_watchdog"] is None
