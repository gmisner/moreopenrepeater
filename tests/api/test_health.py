import os
import subprocess
from pathlib import Path

from api.health import (
    AUDIO_GRACE_SECONDS,
    CRASH_ALERT_REPEAT_SECONDS,
    HEARTBEAT_SECONDS,
    HealthMonitor,
    RunMarker,
    SystemProbe,
)
from api.notify import Notifier
from api.persistence import StateStore


def write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def no_vcgencmd(*args, **kwargs):
    raise FileNotFoundError("vcgencmd")


def test_probe_reads_a_pi(tmp_path):
    root = tmp_path / "root"
    data = tmp_path / "data"
    data.mkdir()
    write(root, "sys/class/thermal/thermal_zone0/temp", "61234\n")
    write(root, "sys/devices/platform/soc/soc:firmware/get_throttled", "50005\n")
    write(root, "proc/stat", "cpu  1 2 3\nbtime 1790000000\nprocesses 5\n")
    st = os.stat(data)
    block = root / "sys/dev/block" / f"{os.major(st.st_dev)}:{os.minor(st.st_dev)}"
    disk = root / "sys/devices/mmc/mmcblk0/mmcblk0p2"
    write(disk.parent / "mmcblk0p2", "stat", "100 0 2000 0 50 0 4096 0 0 0 0\n")
    block.parent.mkdir(parents=True)
    block.symlink_to(disk)
    write(root, "sys/fs/ext4/mmcblk0p2/lifetime_write_kbytes", "123456\n")

    readings = SystemProbe(data, sys_root=root, run=no_vcgencmd, env={"MOREOPENREPEATER_PROTECT_SD": "1"}).readings()

    assert readings["temperature_c"] == 61.234
    assert readings["throttled"] == {
        "under_voltage_now": True,
        "under_voltage_occurred": True,
        "throttled_now": True,
        "throttled_occurred": True,
    }
    assert readings["boot_time"] == 1790000000
    assert readings["disk"]["device"] == "mmcblk0p2"
    assert readings["disk"]["written_since_boot"] == 4096 * 512
    assert readings["disk"]["written_lifetime"] == 123456 * 1024
    assert readings["disk"]["total"] > 0
    assert readings["sd_protection"] is True


def test_probe_falls_back_to_vcgencmd_and_copes_without_a_pi(tmp_path):
    def vcgencmd(args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout="throttled=0x10000\n")

    assert SystemProbe(tmp_path, sys_root=tmp_path, run=vcgencmd).throttled() == 0x10000
    readings = SystemProbe(tmp_path, sys_root=tmp_path, run=no_vcgencmd, env={}).readings()
    assert readings["temperature_c"] is None and readings["throttled"] is None and readings["boot_time"] is None
    assert readings["disk"]["written_since_boot"] is None and readings["sd_protection"] is False


class FakeProbe:
    def __init__(self):
        self.temperature = None
        self.flags = None
        self.boot = None

    def temperature_c(self):
        return self.temperature

    def throttled(self):
        return self.flags

    def boot_time(self):
        return self.boot


def make_monitor(store=None, **kwargs):
    clock = {"now": 10_000.0}
    probe = FakeProbe()
    monitor = HealthMonitor(Notifier(), probe, RunMarker(store, clock=lambda: clock["now"]), clock=lambda: clock["now"], **kwargs)
    return monitor, probe, clock


def titles(alerts):
    return [a.title for a in alerts]


def test_first_start_and_clean_restarts_dont_alert(tmp_path):
    store = StateStore(tmp_path / "run.json")
    monitor, _, clock = make_monitor(store)
    assert monitor.started() == []
    monitor.stopping()

    monitor, _, clock = make_monitor(store)
    assert monitor.started() == []


def test_a_crash_is_reported_once_per_crash_loop(tmp_path):
    store = StateStore(tmp_path / "run.json")
    monitor, probe, clock = make_monitor(store)
    monitor.started()  # and then dies without stopping()

    monitor, probe, clock = make_monitor(store)
    clock["now"] += 120
    probe.boot = 1_000.0  # booted long before
    alerts = monitor.started()
    assert titles(alerts) == ["Restarted after a crash"]
    assert "crashed" in alerts[0].message and alerts[0].severity == "critical"

    for _ in range(3):  # a crash loop
        monitor, probe, clock = make_monitor(store)
        clock["now"] += 180
        assert monitor.started() == []

    monitor, probe, clock = make_monitor(store)
    clock["now"] += CRASH_ALERT_REPEAT_SECONDS + 200
    alerts = monitor.started()
    assert titles(alerts) == ["Restarted after a crash"]
    assert "3 more times" in alerts[0].message


def test_a_power_cut_is_told_apart_from_a_crash(tmp_path):
    store = StateStore(tmp_path / "run.json")
    monitor, probe, clock = make_monitor(store)
    monitor.started()

    monitor, probe, clock = make_monitor(store)
    clock["now"] += 3600
    probe.boot = 10_000.0 + 3000  # booted after the last heartbeat
    alerts = monitor.started()

    assert titles(alerts) == ["Restarted after a power cut"]
    assert "up to 60 minutes" in alerts[0].message
    assert monitor.last_unexpected_stop["power"] is True


def test_heartbeat(tmp_path):
    store = StateStore(tmp_path / "run.json")
    monitor, _, clock = make_monitor(store)
    monitor.started()

    clock["now"] += HEARTBEAT_SECONDS
    monitor.check()

    assert store.load()["heartbeat"] == clock["now"]


def test_temperature_alerts_once_and_clears_with_hysteresis():
    monitor, probe, _ = make_monitor()
    monitor.started()
    monitor.notifier.update(temperature_limit_c=75)

    probe.temperature = 76.0
    assert titles(monitor.check()) == ["CPU temperature 76 °C"]
    probe.temperature = 80.0
    assert monitor.check() == []
    probe.temperature = 72.0
    assert monitor.check() == []
    probe.temperature = 69.0
    assert titles(monitor.check()) == ["CPU temperature back to normal"]


def test_power_and_throttling_flags_alert_once_each():
    monitor, probe, _ = make_monitor()
    monitor.started()

    probe.flags = 0x1 | 0x10000
    assert titles(monitor.check()) == ["Under-voltage"]
    probe.flags = 0x10000
    assert monitor.check() == []
    probe.flags = 0x10000 | 0x40000
    assert titles(monitor.check()) == ["The Pi slowed itself down"]


def test_audio_stopping_after_a_grace_period_and_coming_back():
    audio = {"enabled": True, "running": False, "error": "couldn't open audio devices: no such device"}
    monitor, _, clock = make_monitor(audio_status=lambda: audio)
    monitor.started()

    assert monitor.check() == []
    clock["now"] += AUDIO_GRACE_SECONDS
    alerts = monitor.check()
    assert titles(alerts) == ["Live audio stopped"] and "no such device" in alerts[0].message
    assert monitor.check() == []

    audio["running"] = True
    assert titles(monitor.check()) == ["Live audio is running again"]


def test_turning_audio_off_is_not_a_failure():
    audio = {"enabled": False, "running": False, "error": None}
    monitor, _, clock = make_monitor(audio_status=lambda: audio)
    clock["now"] += 3600
    assert monitor.check() == []


def test_cm108_unplugged():
    present = {"yes": True}
    monitor, _, _ = make_monitor(cm108_path=lambda: "/dev/hidraw0", path_exists=lambda path: present["yes"])

    assert monitor.check() == []
    present["yes"] = False
    assert titles(monitor.check()) == ["The USB radio interface disappeared"]
    assert monitor.check() == []
    present["yes"] = True
    assert titles(monitor.check()) == ["The USB radio interface is back"]


def test_update_results_alert_once_and_old_ones_never(tmp_path):
    status = {"state": "succeeded", "channel": "stable", "started_at": 500.0, "to_sha": "abcdef123456"}
    store = StateStore(tmp_path / "run.json")
    monitor, _, _ = make_monitor(store, update_status=lambda: status)
    monitor.started()
    assert monitor.check() == []  # finished before this install started watching

    status.update(state="running", started_at=600.0)
    assert monitor.check() == []
    status.update(state="rolled_back", message="The new version didn't start; went back to abc1234.")
    alerts = monitor.check()
    assert titles(alerts) == ["Update rolled back"] and "went back" in alerts[0].message

    monitor, _, _ = make_monitor(store, update_status=lambda: status)  # restarted by the rollback
    monitor.started()
    assert monitor.check() == []

    status.update(state="succeeded", started_at=700.0)
    alerts = monitor.check()
    assert titles(alerts) == ["Updated"] and alerts[0].message == "Installed abcdef1 from the stable channel."
