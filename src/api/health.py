"""How the Pi itself is doing, and alerts when it needs attention.

  - `SystemProbe` reads CPU temperature, Raspberry Pi under-voltage and
    throttling flags, and how much has been written to the SD card. Every
    reading is optional: off a Pi (or in CI) the files just aren't there.
  - `RunMarker` notices crashes and power cuts. The service writes a
    heartbeat now and then and marks a clean stop on the way out, so
    finding no clean stop at startup means it died.
  - `HealthMonitor` turns readings into alerts, sent once when a problem
    starts and (where it can end) once when it's over.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Callable, Optional

from .notify import Alert, Notifier
from .persistence import StateStore

HEARTBEAT_SECONDS = 300
CHECK_SECONDS = 30
AUDIO_GRACE_SECONDS = 60
TEMPERATURE_HYSTERESIS_C = 5.0
CRASH_ALERT_REPEAT_SECONDS = 30 * 60  # a crash loop alerts once, not every restart

# get_throttled bits: the low ones are "now", these are "since boot".
UNDER_VOLTAGE_NOW = 1 << 0
THROTTLED_NOW = 1 << 2
UNDER_VOLTAGE_OCCURRED = 1 << 16
FREQUENCY_CAPPED_OCCURRED = 1 << 17
THROTTLED_OCCURRED = 1 << 18
SECTOR_BYTES = 512


def _read(path: Path) -> Optional[str]:
    try:
        return path.read_text().strip()
    except (OSError, UnicodeDecodeError):
        return None


def format_time(at: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(at))


def format_span(seconds: float) -> str:
    minutes = round(seconds / 60)
    if minutes < 90:
        return f"{minutes} minute{'' if minutes == 1 else 's'}"
    return f"{seconds / 3600:.1f} hours"


class SystemProbe:
    """`sys_root` stands in for / in tests."""

    def __init__(
        self,
        data_dir: Path,
        sys_root: Path = Path("/"),
        run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
        env: Optional[dict] = None,
    ) -> None:
        self.data_dir = data_dir
        self._root = sys_root
        self._run = run
        self._env = os.environ if env is None else env

    def temperature_c(self) -> Optional[float]:
        text = _read(self._root / "sys/class/thermal/thermal_zone0/temp")
        try:
            return int(text) / 1000 if text else None
        except ValueError:
            return None

    def throttled(self) -> Optional[int]:
        text = _read(self._root / "sys/devices/platform/soc/soc:firmware/get_throttled")
        if text is None:
            try:
                result = self._run(["vcgencmd", "get_throttled"], capture_output=True, text=True, timeout=5)
            except (OSError, subprocess.SubprocessError):
                return None
            if result.returncode != 0:
                return None
            text = result.stdout.strip()
        try:
            return int(text.rpartition("=")[2], 16)  # "throttled=0x50000", or bare hex from sysfs
        except ValueError:
            return None

    def boot_time(self) -> Optional[float]:
        for line in (_read(self._root / "proc/stat") or "").splitlines():
            if line.startswith("btime "):
                return float(line.split()[1])
        return None

    def disk(self) -> Optional[dict]:
        """The block device holding the data directory: what's been written
        to it since boot and over the filesystem's life (ext4 only), and
        the free space."""
        try:
            st = os.stat(self.data_dir)
            usage = shutil.disk_usage(self.data_dir)
        except OSError:
            return None
        device = f"{os.major(st.st_dev)}:{os.minor(st.st_dev)}"
        block = self._root / "sys/dev/block" / device
        name = block.resolve().name if block.exists() else None
        since_boot = None
        fields = (_read(block / "stat") or "").split()
        if len(fields) > 6 and fields[6].isdigit():
            since_boot = int(fields[6]) * SECTOR_BYTES
        lifetime_text = _read(self._root / "sys/fs/ext4" / name / "lifetime_write_kbytes") if name else None
        return {
            "device": name,
            "written_since_boot": since_boot,
            "written_lifetime": int(lifetime_text) * 1024 if lifetime_text and lifetime_text.isdigit() else None,
            "total": usage.total,
            "free": usage.free,
        }

    def readings(self) -> dict:
        throttled = self.throttled()
        return {
            "temperature_c": self.temperature_c(),
            "throttled": None if throttled is None else {
                "under_voltage_now": bool(throttled & UNDER_VOLTAGE_NOW),
                "under_voltage_occurred": bool(throttled & UNDER_VOLTAGE_OCCURRED),
                "throttled_now": bool(throttled & THROTTLED_NOW),
                "throttled_occurred": bool(throttled & (THROTTLED_OCCURRED | FREQUENCY_CAPPED_OCCURRED)),
            },
            "boot_time": self.boot_time(),
            "disk": self.disk(),
            "sd_protection": self._env.get("MOREOPENREPEATER_PROTECT_SD") == "1",
            "watchdog": self._env.get("WATCHDOG_USEC", "0") not in ("", "0"),
            "hardware_watchdog": self.hardware_watchdog(),
        }

    def hardware_watchdog(self) -> Optional[bool]:
        """Whether systemd is feeding the board's watchdog, which reboots a
        hung Pi; None without one."""
        state = _read(self._root / "sys/class/watchdog/watchdog0/state")
        return None if state is None else state == "active"


class RunMarker:
    """`store=None` keeps it in memory (tests)."""

    def __init__(self, store: Optional[StateStore], clock: Callable[[], float] = time.time) -> None:
        self._store = store
        self._clock = clock
        self.state: dict = {}

    def _save(self) -> None:
        if self._store is not None:
            self._store.save(self.state)

    def start(self) -> Optional[dict]:
        """Returns what the previous run left behind, if anything."""
        previous = self._store.load() if self._store is not None else None
        now = self._clock()
        self.state = {**(previous or {}), "started_at": now, "heartbeat": now, "clean": False}
        self._save()
        return previous

    def beat(self) -> None:
        self.state["heartbeat"] = self._clock()
        self._save()

    def stop(self) -> None:
        self.state["heartbeat"] = self._clock()
        self.state["clean"] = True
        self._save()

    def remember(self, **values: object) -> None:
        self.state.update(values)
        self._save()

    def forget(self, *keys: str) -> None:
        for key in keys:
            self.state.pop(key, None)
        self._save()


def lockout_alert(locked_out: bool) -> Alert:
    if locked_out:
        return Alert(
            "lockout",
            "Locked out: stuck carrier",
            "The receiver kept timing out, so the repeater has stopped repeating (IDs still go out). "
            "It clears by itself once the channel is quiet, or clear it on the dashboard.",
            "critical",
        )
    return Alert("lockout", "Lockout cleared", "The stuck-carrier lockout is over; the repeater is repeating again.", "info")


class HealthMonitor:
    def __init__(
        self,
        notifier: Notifier,
        probe: SystemProbe,
        marker: RunMarker,
        audio_status: Callable[[], dict] = lambda: {"enabled": False},
        cm108_path: Callable[[], Optional[str]] = lambda: None,
        update_status: Callable[[], Optional[dict]] = lambda: None,
        clock: Callable[[], float] = time.time,
        path_exists: Callable[[str], bool] = os.path.exists,
    ) -> None:
        self.notifier = notifier
        self.probe = probe
        self.marker = marker
        self._audio_status = audio_status
        self._cm108_path = cm108_path
        self._update_status = update_status
        self._clock = clock
        self._path_exists = path_exists
        self._last_beat = 0.0
        self._hot = False
        self._throttle_seen = 0
        self._audio_down_since: Optional[float] = None
        self._audio_alerted = False
        self._cm108_gone = False
        self.last_unexpected_stop: Optional[dict] = None

    def started(self) -> list[Alert]:
        now = self._clock()
        previous = self.marker.start()
        self._last_beat = now
        alerts = []
        if previous is None:
            status = self._update_status()
            self.marker.remember(update_seen=(status or {}).get("started_at", 0))
            return alerts
        stalled = previous.get("watchdog")
        if stalled:
            self.marker.forget("watchdog")
        if previous.get("clean", True):
            return alerts
        last_seen = previous.get("heartbeat", previous.get("started_at", now))
        boot = self.probe.boot_time()
        power = boot is not None and boot > last_seen
        self.last_unexpected_stop = {"last_seen": last_seen, "restarted_at": now, "power": power, "watchdog": stalled}
        alerted_at = previous.get("crash_alerted_at", 0)
        missed = previous.get("crashes_since_alert", 0)
        if now - alerted_at < CRASH_ALERT_REPEAT_SECONDS:
            self.marker.remember(crashes_since_alert=missed + 1)
            return alerts
        if stalled and not power:
            cause = f"The watchdog restarted moreopenrepeater because the {stalled} stopped responding"
        elif power:
            cause = "The Pi lost power or restarted without shutting down"
        else:
            cause = "moreopenrepeater stopped unexpectedly (it crashed or was killed)"
        message = (
            f"{cause}. It was last known to be running at {format_time(last_seen)} "
            f"and started again at {format_time(now)}, so it was off the air for up to {format_span(now - last_seen)}."
        )
        if missed:
            message += f" It has also restarted unexpectedly {missed} more time{'' if missed == 1 else 's'} since the last alert."
        self.marker.remember(crash_alerted_at=now, crashes_since_alert=0)
        title = "Restarted after a power cut" if power else "Restarted by the watchdog" if stalled else "Restarted after a crash"
        alerts.append(Alert("restart", title, message, "critical"))
        return alerts

    def stopping(self) -> None:
        self.marker.stop()

    def check(self) -> list[Alert]:
        now = self._clock()
        if now - self._last_beat >= HEARTBEAT_SECONDS:
            self.marker.beat()
            self._last_beat = now
        return self._temperature() + self._throttling() + self._audio(now) + self._cm108() + self._update()

    def _temperature(self) -> list[Alert]:
        temperature = self.probe.temperature_c()
        limit = self.notifier.settings.temperature_limit_c
        if temperature is None:
            return []
        if not self._hot and temperature >= limit:
            self._hot = True
            return [Alert(
                "temperature",
                f"CPU temperature {temperature:.0f} °C",
                f"The Pi's CPU has reached {temperature:.0f} °C (the alert limit is {limit:.0f} °C). "
                "It slows itself down at 80–85 °C. Check the enclosure's ventilation and the sun on it.",
            )]
        if self._hot and temperature < limit - TEMPERATURE_HYSTERESIS_C:
            self._hot = False
            return [Alert("temperature", "CPU temperature back to normal", f"The Pi's CPU is down to {temperature:.0f} °C.", "info")]
        return []

    def _throttling(self) -> list[Alert]:
        flags = self.probe.throttled()
        if flags is None:
            return []
        new = flags & ~self._throttle_seen
        self._throttle_seen |= flags
        alerts = []
        if new & UNDER_VOLTAGE_OCCURRED:
            alerts.append(Alert(
                "power",
                "Under-voltage",
                "The Pi's supply voltage has dropped too low since it booted. That can corrupt the SD card and reset "
                "the USB radio interface. Use a better power supply, or a shorter, thicker cable.",
                "critical",
            ))
        if new & (THROTTLED_OCCURRED | FREQUENCY_CAPPED_OCCURRED):
            alerts.append(Alert(
                "throttled",
                "The Pi slowed itself down",
                "The Pi has throttled its CPU since it booted (from heat or low voltage), which can make the audio stutter.",
            ))
        return alerts

    def _audio(self, now: float) -> list[Alert]:
        status = self._audio_status()
        if not status.get("enabled"):
            self._audio_down_since = None
            self._audio_alerted = False
            return []
        if status.get("running"):
            self._audio_down_since = None
            if self._audio_alerted:
                self._audio_alerted = False
                return [Alert("audio", "Live audio is running again", "The audio engine is running again.", "info")]
            return []
        if self._audio_down_since is None:
            self._audio_down_since = now
        if self._audio_alerted or now - self._audio_down_since < AUDIO_GRACE_SECONDS:
            return []
        self._audio_alerted = True
        error = status.get("error") or "The audio engine isn't running."
        return [Alert("audio", "Live audio stopped", f"{error} The repeater can't hear or transmit until it's fixed.", "critical")]

    def _cm108(self) -> list[Alert]:
        path = self._cm108_path()
        if not path:
            return []
        present = self._path_exists(path)
        if not present and not self._cm108_gone:
            self._cm108_gone = True
            return [Alert(
                "cm108",
                "The USB radio interface disappeared",
                f"{path} is gone: the CM108 interface was unplugged or reset, so PTT and COS don't work. "
                "Plug it back in, then restart moreopenrepeater.",
                "critical",
            )]
        if present and self._cm108_gone:
            self._cm108_gone = False
            return [Alert("cm108", "The USB radio interface is back", "Restart moreopenrepeater to use it again.", "info")]
        return []

    def _update(self) -> list[Alert]:
        status = self._update_status()
        if not status or status.get("state") not in ("succeeded", "failed", "rolled_back"):
            return []
        started_at = status.get("started_at", 0)
        if started_at <= self.marker.state.get("update_seen", 0):
            return []
        self.marker.remember(update_seen=started_at)
        if status["state"] == "succeeded":
            sha = (status.get("to_sha") or "")[:7] or "the latest version"
            channel = f" from the {status['channel']} channel" if status.get("channel") else ""
            return [Alert("update", "Updated", f"Installed {sha}{channel}.", "info")]
        title = "Update rolled back" if status["state"] == "rolled_back" else "Update failed"
        return [Alert("update", title, status.get("message") or "See the Updates page for the log.")]
