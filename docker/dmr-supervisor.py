#!/usr/bin/env python3
"""Runs the DVSwitch DMR bridge in a container: three programs, kept running, told what to do by a request file.

On a Pi the bridge is three systemd units (md380-emu, Analog_Bridge, MMDVM_Bridge) and a root helper that restarts them
when the dashboard asks (scripts/dmr-restart.sh). A container has neither systemd nor a root helper, so this is both: it
starts the three, starts one again three seconds after it exits (as the units did), and honors the same request file
and status file the dashboard already writes and reads (`src/api/dmr_restart.py`), so nothing in the dashboard changes.

  request file   one word: restart (apply a change to the .ini files), stop, or install. It is removed once read.
  status.json    {state, action, started_at, finished_at, message}: what the dashboard shows after it asks.
  health.json    written every 2 s for the container's health check: when, and which programs are running.

Until the settings are complete (a callsign, a DMR Id, the network's address and the password, which the dashboard asks
for) it starts nothing and says so: the programs exit at once without them, and a restart loop every few seconds is log
noise and wasted CPU. Like the units, it does not restart Analog_Bridge after a configuration error (exit 252 to 254),
and it waits longer between restarts of a program that keeps dying.

It also seeds the configuration. The first time the volume is empty it writes the project's .ini files with the
container's addresses in them (the core is not on 127.0.0.1 here, and nothing should log to a file), and never touches
them again: after that they belong to the owner and the dashboard. Standard library only.
"""
from __future__ import annotations

import configparser
import argparse
import ipaddress
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Mapping, Optional, Sequence

CONFIG_DIR = Path("/etc/moreopenrepeater/dmr")
SEED_DIR = Path("/opt/dmr-seed")
CONTROL_DIR = Path("/dmr-control")
RUN_DIR = Path("/run/dmr")
RESTART_DELAY = 3.0  # what the systemd units used
MAX_RESTART_DELAY = 60.0
QUICK_EXIT_SECONDS = 30.0  # a program that dies sooner than this is failing, not just stopped
CONFIG_ERROR_EXITS = (252, 253, 254)  # Analog_Bridge's "fix the settings": the unit's RestartPreventExitStatus
STOP_TIMEOUT = 10.0
POLL_SECONDS = 0.5
HEALTH_SECONDS = 2.0
HEALTH_FRESH_SECONDS = 10.0
ACTIONS = ("restart", "stop", "install")
NETWORKS = {"brandmeister": "3102.repeater.net", "tgif": "tgif.network", "custom": ""}  # src/api/dmr.py NETWORK_DEFAULTS
CORE_ADDRESS = "172.28.0.10"  # where the core listens for the bridge's audio, unless MOR_CORE_ADDRESS says otherwise


def default_programs(config_dir: Path = CONFIG_DIR) -> dict[str, dict]:
    """The three programs, as the systemd units start them (packaging/dmr/*.service.d.conf), with the .ini files
    the owner's configuration volume holds."""
    return {
        "md380_emu": {
            "argv": ["/opt/md380-emu/qemu-arm-static", "/opt/md380-emu/md380-emu", "-S", "2470"],
            "cwd": "/opt/md380-emu",
            "env": {},
        },
        "analog_bridge": {
            "argv": ["/opt/Analog_Bridge/Analog_Bridge", str(config_dir / "Analog_Bridge.ini")],
            "cwd": "/opt/Analog_Bridge",
            "env": {"AnalogBridgeLogDir": "/tmp/dvswitch"},
        },
        "mmdvm_bridge": {
            "argv": ["/opt/MMDVM_Bridge/MMDVM_Bridge", str(config_dir / "MMDVM_Bridge.ini")],
            "cwd": "/opt/MMDVM_Bridge",
            "env": {"DVSWITCH": str(config_dir / "DVSwitch.ini")},
        },
    }


# -- the configuration the container starts with ------------------------------------------------------------------


def set_ini_value(text: str, section: str, key: str, value: str) -> str:
    """`text` with `key` in `[section]` set to `value`, changing that one line and nothing else. The key must be there:
    a seed that no longer matches the program's file is an error, not a silent no-op."""
    lines = text.splitlines(keepends=True)
    current = None
    for number, line in enumerate(lines):
        header = re.match(r"^\s*\[([^\]]+)\]\s*$", line)
        if header:
            current = header.group(1)
            continue
        if current == section:
            match = re.match(rf"^([ \t]*{re.escape(key)}[ \t]*=[ \t]*)(.*?)(\r?\n?)$", line)  # spaces and tabs only: not the newline
            if match:
                lines[number] = f"{match.group(1)}{value}{match.group(3)}"
                return "".join(lines)
    raise ValueError(f"no {key} in [{section}]")


def seed_config(
    config_dir: Path = CONFIG_DIR, seed_dir: Path = SEED_DIR, core_address: str = CORE_ADDRESS, macro: Optional[Path] = None
) -> list[str]:
    """Write the .ini files that are missing, with the container's addresses, and return their names. Files that exist are
    never touched. The Analog_Bridge macro file is linked from the package, as the installer does."""
    try:
        ipaddress.ip_address(core_address)
    except ValueError:
        raise ValueError(f"MOR_CORE_ADDRESS is not an IP address: {core_address!r}") from None
    config_dir.mkdir(parents=True, exist_ok=True)
    written = []
    edits = {
        "Analog_Bridge.ini": [("USRP", "address", core_address)],  # the core, which is not on the loopback here
        "MMDVM_Bridge.ini": [("Log", "FileLevel", "0"), ("Log", "FilePath", "/tmp")],  # the console only: no log file on the disk
    }
    for name in ("Analog_Bridge.ini", "MMDVM_Bridge.ini", "DVSwitch.ini"):
        target = config_dir / name
        if target.exists():
            continue
        text = (seed_dir / name).read_text()
        for section, key, value in edits.get(name, []):
            text = set_ini_value(text, section, key, value)
        target.write_text(text)
        target.chmod(0o600)  # the network password goes in here
        written.append(name)
    link = config_dir / "dvsm.macro"
    source = macro or Path("/opt/Analog_Bridge/dvsm.macro")
    if source.exists() and not link.exists() and not link.is_symlink():
        link.symlink_to(source)
    return written


class ConfigureError(ValueError):
    pass


def _number(value, name: str, low: int, high: int) -> int:
    try:
        number = int(str(value))
    except ValueError:
        raise ConfigureError(f"{name} must be a number from {low} to {high}") from None
    if not low <= number <= high:
        raise ConfigureError(f"{name} must be a number from {low} to {high}")
    return number


def configure(
    config_dir: Path, *, callsign: str, dmr_id: str, network: str, password: str, host: str = "", port: int = 62031,
    talkgroup: int = 9, slot: int = 2, color_code: int = 1, ssid: str = "01",
) -> None:
    """Write the identity and network into the three .ini files, the same keys the dashboard's DMR page writes
    (src/api/dmr.py), for an install whose dashboard does not edit them yet. Everything is checked before anything is
    written, because these values go into files another program parses: no control character, no odd characters."""
    callsign = callsign.strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{3,10}", callsign):
        raise ConfigureError("the callsign is letters and digits, 3 to 10 of them")
    if not re.fullmatch(r"[0-9]{7}", str(dmr_id)):
        raise ConfigureError("the DMR Id is 7 digits")
    if not re.fullmatch(r"[0-9]{2}", ssid):
        raise ConfigureError("the repeater suffix is 2 digits")
    if network not in NETWORKS:
        raise ConfigureError("the network is brandmeister, tgif or custom")
    host = NETWORKS[network] if network != "custom" else host.strip()
    if not host or not re.fullmatch(r"[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?", host):
        raise ConfigureError("a custom network needs its master's host name or address")
    if not password or any(ord(c) < 32 or ord(c) == 127 for c in password) or len(password) > 128:
        raise ConfigureError("the network password is required (up to 128 characters, no control characters)")
    port = _number(port, "the port", 1, 65535)
    talkgroup = _number(talkgroup, "the talkgroup", 0, 16777215)
    slot = _number(slot, "the slot", 1, 2)
    color_code = _number(color_code, "the colour code", 0, 15)
    repeater = f"{dmr_id}{ssid}"
    for name in ("Analog_Bridge.ini", "MMDVM_Bridge.ini", "DVSwitch.ini"):
        if not (config_dir / name).is_file():
            raise ConfigureError(f"{config_dir / name} is not there: start the container once so it is written")

    def apply(name: str, changes: list[tuple[str, str, str]]) -> None:
        path = config_dir / name
        text = path.read_text()
        for section, key, value in changes:
            text = set_ini_value(text, section, key, value)
        temporary = path.with_name(path.name + ".new")
        temporary.write_text(text)
        temporary.chmod(0o600)
        os.replace(temporary, path)

    apply("Analog_Bridge.ini", [
        ("AMBE_AUDIO", "gatewayDmrId", str(dmr_id)), ("AMBE_AUDIO", "repeaterID", repeater), ("AMBE_AUDIO", "txTg", str(talkgroup)),
        ("AMBE_AUDIO", "txTs", str(slot)), ("AMBE_AUDIO", "colorCode", str(color_code)), ("AMBE_AUDIO", "ambeMode", "DMR"),
    ])  # the [USRP] address is the container's own and is left as the seed wrote it
    apply("MMDVM_Bridge.ini", [
        ("General", "Callsign", callsign), ("General", "Id", repeater), ("General", "Duplex", "0"),
        ("DMR", "Enable", "1"), ("DMR", "ColorCode", str(color_code)),
        ("DMR Network", "Enable", "1"), ("DMR Network", "Address", host), ("DMR Network", "Port", str(port)),
        ("DMR Network", "Password", password), ("DMR Network", "Slot1", "1" if slot == 1 else "0"),
        ("DMR Network", "Slot2", "1" if slot == 2 else "0"),
    ])
    apply("DVSwitch.ini", [("DMR", "slot", str(slot))])


def missing_settings(config_dir: Path = CONFIG_DIR) -> list[str]:
    """What the bridge cannot run without and the .ini files do not yet have, as the dashboard counts it
    (src/api/dmr.py): the callsign, the DMR Id, the network's address and the network password. Empty when complete."""
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        parser.read_string((config_dir / "MMDVM_Bridge.ini").read_text())
    except (OSError, configparser.Error):
        return ["MMDVM_Bridge.ini"]
    def get(section: str, key: str) -> str:
        return parser.get(section, key, fallback="").strip()
    missing = []
    if get("General", "Callsign") in ("", "N0CALL"):
        missing.append("callsign")
    if not get("General", "Id").lstrip("0"):
        missing.append("DMR Id")
    if not get("DMR Network", "Address"):
        missing.append("network address")
    if not get("DMR Network", "Password"):
        missing.append("network password")
    return missing


# -- the programs -------------------------------------------------------------------------------------------------


class Supervisor:
    def __init__(
        self,
        programs: Mapping[str, dict],
        *,
        control_dir: Path = CONTROL_DIR,
        run_dir: Path = RUN_DIR,
        clock: Callable[[], float] = time.time,
        restart_delay: float = RESTART_DELAY,
        stop_timeout: float = STOP_TIMEOUT,
        missing: Callable[[], list[str]] = lambda: [],
        out=sys.stdout,
    ) -> None:
        self.programs = dict(programs)
        self.control_dir = control_dir
        self.run_dir = run_dir
        self._clock = clock
        self.restart_delay = restart_delay
        self.stop_timeout = stop_timeout
        self._out = out
        self._children: dict[str, subprocess.Popen] = {}
        self._exited_at: dict[str, float] = {}
        self._started_at: dict[str, float] = {}
        self._failures: dict[str, int] = {}
        self._gave_up: set[str] = set()  # exited with a configuration error: not started again until asked
        self._missing = missing
        self.waiting: list[str] = []  # what the settings still lack: nothing is started while this is not empty
        self.stopped = False  # asked to stop: stay down until asked to restart
        self.stopping = False  # the container is shutting down
        self._last_health = 0.0
        self._lock = threading.Lock()

    @property
    def request_path(self) -> Path:
        return self.control_dir / "request"

    @property
    def status_path(self) -> Path:
        return self.control_dir / "status.json"

    @property
    def health_path(self) -> Path:
        return self.run_dir / "health.json"

    # -- output: what the programs say goes to the container's log, named, one line at a time --------------------

    def _log(self, name: str, line: str) -> None:
        with self._lock:
            print(f"[{name}] {line.rstrip()}", file=self._out, flush=True)

    def _pump(self, name: str, stream) -> None:
        for raw in iter(stream.readline, b""):
            self._log(name, raw.decode("utf-8", "replace"))

    # -- starting and stopping ------------------------------------------------------------------------------------

    def start(self, name: str) -> None:
        spec = self.programs[name]
        try:
            child = subprocess.Popen(
                spec["argv"], cwd=spec.get("cwd"), env={**os.environ, **spec.get("env", {})},
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            )
        except OSError as error:
            self._log("supervisor", f"could not start {name}: {error.strerror or error}")
            self._exited_at[name] = self._clock()
            return
        self._children[name] = child
        self._started_at[name] = self._clock()
        self._exited_at.pop(name, None)
        threading.Thread(target=self._pump, args=(name, child.stdout), daemon=True).start()
        self._log("supervisor", f"started {name} (pid {child.pid})")

    def start_all(self) -> None:
        """Start what is not running, if the settings are complete; otherwise say what is missing, once."""
        missing = self._missing()
        if missing:
            if missing != self.waiting:
                self._log("supervisor", "waiting for the DMR settings: " + ", ".join(missing) + " (set them in the dashboard)")
            self.waiting = list(missing)
            return
        if self.waiting:
            self._log("supervisor", "the DMR settings are complete")
        self.waiting = []
        self._gave_up.clear()
        for name in self.programs:
            if name not in self._children:
                self.start(name)

    def stop_all(self) -> None:
        children = list(self._children.items())
        for _, child in children:
            if child.poll() is None:
                child.terminate()
        deadline = self._clock() + self.stop_timeout
        for name, child in children:
            while child.poll() is None and self._clock() < deadline:
                time.sleep(0.05)
            if child.poll() is None:
                child.kill()
                child.wait()
                self._log("supervisor", f"{name} did not stop in {self.stop_timeout:g} s and was killed")
        self._children.clear()
        self._exited_at.clear()
        self._failures.clear()

    def running(self) -> dict[str, bool]:
        return {name: name in self._children and self._children[name].poll() is None for name in self.programs}

    # -- the loop -------------------------------------------------------------------------------------------------

    def tick(self) -> None:
        """One turn: bring back anything that exited, answer a request, say how things are."""
        now = self._clock()
        if not self.stopped and not self.stopping:
            if self.waiting and not self._missing():
                self.start_all()  # the settings were completed without a restart request: go ahead
            for name in self.programs:
                child = self._children.get(name)
                if child is not None and child.poll() is not None:
                    self._note_exit(name, child.returncode, now)
                if (
                    name not in self._children and name not in self._gave_up and not self.waiting
                    and name in self._exited_at and now - self._exited_at[name] >= self._delay(name)
                ):
                    self.start(name)
        self.handle_request()
        if now - self._last_health >= HEALTH_SECONDS:
            self._last_health = now
            self.write_health()

    def _delay(self, name: str) -> float:
        """3 s, as the units had, doubling for a program that keeps dying, up to a minute."""
        failures = self._failures.get(name, 0)
        return min(self.restart_delay * (2 ** max(0, failures - 1)), MAX_RESTART_DELAY)

    def _note_exit(self, name: str, status: int, now: float) -> None:
        del self._children[name]
        self._exited_at[name] = now
        quick = now - self._started_at.get(name, now) < QUICK_EXIT_SECONDS
        self._failures[name] = self._failures.get(name, 0) + 1 if quick else 0
        if name == "analog_bridge" and status in CONFIG_ERROR_EXITS:
            self._gave_up.add(name)
            self._log("supervisor", f"{name} exited with a configuration error (status {status}); not starting it again until the settings change and a restart is asked for")
        else:
            self._log("supervisor", f"{name} exited with status {status}; starting it again in {self._delay(name):g} s")

    def handle_request(self) -> Optional[str]:
        """The dashboard's request, if there is one: the action it names, after doing it. A request that is not a plain
        file, or names nothing we know, is removed and ignored (as the root helper's script does)."""
        path = self.request_path
        try:
            if path.is_symlink() or not path.is_file():
                if path.is_symlink():
                    path.unlink(missing_ok=True)
                return None
            action = path.read_text(errors="replace").strip().split("\n", 1)[0].strip().lower()
            path.unlink(missing_ok=True)
        except OSError:
            return None
        if action not in ACTIONS:
            self._log("supervisor", "ignored a request it does not know")
            return None
        started = self._clock()
        self.write_status("running", action, started, f"{action} requested")
        if action == "stop":
            self.stopped = True
            self.stop_all()
            self.write_status("succeeded", action, started, "The DMR bridge is stopped.")
        elif action == "restart":
            self.stop_all()
            self.stopped = False
            self.start_all()
            if self.waiting:
                self.write_status("failed", action, started, "Not started: the DMR settings lack the " + ", ".join(self.waiting) + ".")
            else:
                self.write_status("succeeded", action, started, "The DMR bridge was restarted with the current settings.")
        else:  # install: the programs are part of this container's image
            self.write_status("failed", action, started, "DVSwitch is built into the DMR container, so there is nothing to install.")
        return action

    def write_status(self, state: str, action: str, started: float, message: str) -> None:
        status = {"state": state, "action": action, "started_at": started, "message": message}
        if state != "running":
            status["finished_at"] = self._clock()
        self._atomic(self.status_path, status, 0o644)

    def write_health(self) -> None:
        self._atomic(
            self.health_path,
            {"time": self._clock(), "stopped": self.stopped, "waiting": self.waiting, "running": self.running()},
            0o644,
        )

    def _atomic(self, path: Path, data: dict, mode: int) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(path.name + ".new")
            temporary.write_text(json.dumps(data))
            temporary.chmod(mode)
            os.replace(temporary, path)
        except OSError as error:
            self._log("supervisor", f"could not write {path.name}: {error.strerror or error}")

    def run(self) -> int:
        def stop(signum, frame):  # SIGTERM from `docker stop`, or Ctrl-C
            self.stopping = True

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        self.start_all()
        self.write_health()
        while not self.stopping:
            self.tick()
            time.sleep(POLL_SECONDS)
        self._log("supervisor", "shutting down")
        self.stop_all()
        return 0


# -- the health check ---------------------------------------------------------------------------------------------


def check(run_dir: Path = RUN_DIR, clock: Callable[[], float] = time.time) -> tuple[bool, str]:
    """Healthy when the supervisor is alive (a fresh heartbeat) and every program is running, or it was asked to stop."""
    try:
        health = json.loads((run_dir / "health.json").read_text())
    except (OSError, ValueError):
        return False, "no heartbeat from the supervisor"
    if clock() - float(health.get("time", 0)) > HEALTH_FRESH_SECONDS:
        return False, "the supervisor's heartbeat is stale"
    if health.get("stopped"):
        return True, "stopped by request"
    if health.get("waiting"):  # nothing to run yet: not a fault, the owner has not entered the settings
        return True, "waiting for the DMR settings: " + ", ".join(health["waiting"])
    down = sorted(name for name, up in health.get("running", {}).items() if not up)
    return (not down), ("not running: " + ", ".join(down)) if down else "running"


def configure_command(argv: Sequence[str], env: Mapping[str, str]) -> int:
    """`dmr-supervisor.py --configure --callsign W1AW --dmr-id 1234567 --network brandmeister`, with the network password in
    the DMR_PASSWORD environment variable (never an argument: arguments end up in process lists and shell history).
    Writes the settings and asks the running supervisor to restart the bridge."""
    parser = argparse.ArgumentParser(prog="dmr-supervisor.py --configure", description=configure_command.__doc__)
    parser.add_argument("--callsign", required=True)
    parser.add_argument("--dmr-id", required=True)
    parser.add_argument("--network", required=True, choices=sorted(NETWORKS))
    parser.add_argument("--host", default="", help="the master's host name, for --network custom")
    parser.add_argument("--port", default=62031)
    parser.add_argument("--talkgroup", default=9)
    parser.add_argument("--slot", default=2)
    parser.add_argument("--color-code", default=1)
    parser.add_argument("--suffix", default="01", help="the repeater suffix added to the DMR Id (two digits)")
    args = parser.parse_args(list(argv))
    try:
        configure(
            CONFIG_DIR, callsign=args.callsign, dmr_id=args.dmr_id, network=args.network, password=env.get("DMR_PASSWORD", ""),
            host=args.host, port=args.port, talkgroup=args.talkgroup, slot=args.slot, color_code=args.color_code, ssid=args.suffix,
        )
    except (ConfigureError, OSError) as error:
        print(f"dmr: {error}" + ("" if env.get("DMR_PASSWORD") else " (set DMR_PASSWORD in the environment)"), file=sys.stderr)
        return 2
    try:
        (CONTROL_DIR / "request").write_text("restart\n")
        print("The DMR settings are written and the bridge is being restarted.")
    except OSError:
        print("The DMR settings are written. Restart the bridge to use them (docker compose restart dmr).")
    return 0


def main(argv: Optional[Sequence[str]] = None, env: Optional[Mapping[str, str]] = None, programs: Optional[dict] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    env = os.environ if env is None else env
    if argv == ["--check"]:
        healthy, message = check()
        print(message)
        return 0 if healthy else 1
    if argv[:1] == ["--configure"]:
        return configure_command(argv[1:], env)
    if argv:
        print("usage: dmr-supervisor.py [--check | --configure ...]", file=sys.stderr)
        return 2
    try:
        written = seed_config(core_address=env.get("MOR_CORE_ADDRESS", CORE_ADDRESS) or CORE_ADDRESS)
    except (ValueError, OSError) as error:
        print(f"dmr: {error}", file=sys.stderr)
        return 2
    if written:
        print(f"[supervisor] wrote {', '.join(written)} in {CONFIG_DIR}", flush=True)
    Path("/tmp/dvswitch").mkdir(parents=True, exist_ok=True)
    return Supervisor(programs if programs is not None else default_programs(), missing=lambda: missing_settings(CONFIG_DIR)).run()


if __name__ == "__main__":
    sys.exit(main())
