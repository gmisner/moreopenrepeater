#!/usr/bin/env python3
"""Runs chronyd in the optional time container: it sets the Pi's clock from the NTP server the owner picked in the dashboard.

The dashboard (the API container) writes the server into `ntp.json` in a volume this container only reads. This program

  - reads that file at start and again whenever it changes, checks the value again (it is about to be written into chrony's
    configuration, so nothing in the file is trusted), renders `chrony.conf` from it and restarts chronyd,
  - keeps chronyd running (starts it again a few seconds after it exits, with a longer wait if it keeps dying), and
  - writes `status.json` into a volume of its own every 30 seconds (`chronyc tracking` over chrony's Unix socket): when,
    whether chronyd is up, and what it sees. The dashboard reads that to say whether this container is running. The API
    never writes there and this container never writes the setting: each side owns only its own file.

An empty or missing setting means the default pool (pool.ntp.org). A setting that fails the check is not used: the container
keeps what it had (or the default at start) and says so in `status.json` (`config_error`).

chronyd runs as the container's root with ONE capability, CAP_SYS_TIME (see docker/compose.yaml for why). It is a client only:
`port 0` and `cmdport 0` in the configuration mean it listens on no network port at all; the only way in is its Unix socket
in a tmpfs, which only this program uses. `makestep 1.0 3` lets it step the clock in its first three updates after every start,
so a Pi that boots without a battery clock (and so with a wrong time) is corrected at once instead of slewed for hours.

Standard library only. `--check` is the container's health check.
"""
from __future__ import annotations

import argparse
import csv
import io
import ipaddress
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

DEFAULT_SERVER = "pool.ntp.org"
MAX_SERVER_LENGTH = 253
RESTART_DELAY = 3.0
MAX_RESTART_DELAY = 60.0
STOP_TIMEOUT = 5.0
MAX_SETTING_BYTES = 4096
HEALTH_FRESH_SECONDS = 90.0  # the status is written every 30 s
CHRONYC_TIMEOUT = 5.0


class ConfigError(ValueError):
    pass


# -- the one value that goes into the configuration ---------------------------------------------------------------------------

_NAME_CHARS = re.compile(r"[A-Za-z0-9.:-]+")
_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")
_IPV4_SHAPE = re.compile(r"[0-9]+(?:\.[0-9]+){3}")


def check_server(value: object) -> str:
    """The value if it is safe to write after `server ` in chrony.conf: empty (the default), an IP address, or a DNS name.
    Anything else raises ConfigError. This is the same rule as the dashboard's (src/api/time_sync.py): the file is
    checked again here because the file is not trusted, whoever wrote it. Letters, digits, dots and hyphens in labels
    that do not start or end with a hyphen, or an IPv6 address; no whitespace, quote, `#`, `;`, `/`, `%` or non-ASCII,
    and never a leading dash."""
    if not isinstance(value, str):
        raise ConfigError("the NTP server must be text")
    if value == "":
        return ""
    if len(value) > MAX_SERVER_LENGTH:
        raise ConfigError("the NTP server name is too long")
    if not value.isascii() or not _NAME_CHARS.fullmatch(value):
        raise ConfigError("the NTP server has characters a host name or IP address cannot have")
    if ":" in value:
        try:
            address = ipaddress.IPv6Address(value)
        except ValueError:
            raise ConfigError("the NTP server is not a valid IPv6 address") from None
        return _usable(address, value)
    if _IPV4_SHAPE.fullmatch(value):
        try:
            address = ipaddress.IPv4Address(value)
        except ValueError:
            raise ConfigError("the NTP server is not a valid IPv4 address") from None
        return _usable(address, value)
    labels = value.split(".")
    if not all(_LABEL.fullmatch(label) for label in labels):
        raise ConfigError("the NTP server is not a valid host name")
    if labels[-1].isdigit() or value.lower() == "localhost":
        raise ConfigError("the NTP server is not a valid host name")
    return value


def _usable(address, value: str) -> str:
    if address.is_unspecified or address.is_multicast or address.is_loopback or address.is_reserved:
        raise ConfigError("the NTP server address cannot be a time server")
    return value


def read_setting(path: Path) -> str:
    """The server in `ntp.json`: empty if the file is absent. Raises ConfigError for anything that is not a good value."""
    try:
        if path.stat().st_size > MAX_SETTING_BYTES:
            raise ConfigError("ntp.json is too big to be right")
        text = path.read_text()
    except FileNotFoundError:
        return ""
    except OSError as error:
        raise ConfigError(f"ntp.json could not be read ({error.strerror or type(error).__name__})") from None
    except UnicodeDecodeError:
        raise ConfigError("ntp.json is not text") from None
    try:
        data = json.loads(text)
    except ValueError:
        raise ConfigError("ntp.json is not valid JSON") from None
    if not isinstance(data, dict) or "server" not in data:
        raise ConfigError('ntp.json has no "server"')
    return check_server(data["server"])


def render_config(server: str, run_dir: Path) -> str:
    """chrony.conf for `server` (already checked; checked again here, because this text is what chronyd will obey)."""
    server = check_server(server)
    source = f"server {server} iburst" if server else f"pool {DEFAULT_SERVER} iburst maxsources 4"
    lines = [
        "# Made by chrony-supervisor.py from ntp.json each time that changes. Edits here are lost.",
        source,
        # chronyd runs as root with only CAP_SYS_TIME: it cannot drop to another user (that needs CAP_SETUID and CAP_SETGID),
        # and an unprivileged user would have no capability at all in a container.
        "user root",
        # A client only: no NTP server port and no command port. The one way in is the Unix socket below.
        "port 0",
        "cmdport 0",
        f"bindcmdaddress {run_dir}/chronyd.sock",
        f"pidfile {run_dir}/chronyd.pid",
        f"driftfile {run_dir}/drift",
        # Step the clock in the first three updates after each start. A Pi has no battery clock, so it can boot hours off.
        "makestep 1.0 3",
        "",
    ]
    return "\n".join(lines)


def write_atomic(path: Path, text: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


# -- what chronyd says -------------------------------------------------------------------------------------------------------------


def parse_tracking(output: str) -> dict:
    """`chronyc -c tracking` (one CSV line): reference id, name, stratum, ref time, system time offset, ... leap status.
    Anything that does not parse gives no facts, never an error."""
    try:
        row = next(csv.reader(io.StringIO(output.strip())))
        stratum = int(row[2])
        offset = abs(float(row[4]))
        leap = row[13].strip()
    except (StopIteration, ValueError, IndexError):
        return {}
    synced = stratum > 0 and leap.lower() != "not synchronised" and row[0].strip("0") != ""
    return {"synced": synced, "stratum": stratum if synced else None, "offset_ms": round(offset * 1000, 3) if synced else None}


def tracking(chronyc: str, socket_path: Path) -> dict:
    try:
        done = subprocess.run(
            [chronyc, "-h", str(socket_path), "-n", "-c", "tracking"], capture_output=True, text=True, timeout=CHRONYC_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    return parse_tracking(done.stdout) if done.returncode == 0 else {}


# -- the supervisor ----------------------------------------------------------------------------------------------------------------


class Supervisor:
    def __init__(self, env: Optional[dict] = None) -> None:
        env = os.environ if env is None else env
        self.config_dir = Path(env.get("MOR_TIME_CONFIG", "/time-config"))
        self.status_dir = Path(env.get("MOR_TIME_STATUS", "/time-status"))
        self.run_dir = Path(env.get("MOR_CHRONY_RUN", "/run/chrony"))
        self.chronyd = env.get("MOR_CHRONYD", "chronyd")
        self.chronyc = env.get("MOR_CHRONYC", "chronyc")
        self.poll = float(env.get("MOR_TIME_POLL_SECONDS", "2"))
        self.status_every = float(env.get("MOR_TIME_STATUS_SECONDS", "30"))
        self.restart_delay = float(env.get("MOR_TIME_RESTART_SECONDS", str(RESTART_DELAY)))
        self.setting_path = self.config_dir / "ntp.json"
        self.conf_path = self.run_dir / "chrony.conf"
        self.stop = threading.Event()
        self.process: Optional[subprocess.Popen] = None
        self.server = ""  # what chronyd is running with
        self.config_error: Optional[str] = None
        self._signature: object = ()  # of ntp.json, to notice a change
        self._failures = 0
        self._started = 0.0
        self._next_start = 0.0

    # -- the setting --

    def _stat(self) -> object:
        try:
            info = self.setting_path.stat()
        except OSError:
            return None
        return (info.st_mtime_ns, info.st_size, info.st_ino)

    def load(self) -> Optional[str]:
        """Read the setting: the server wanted, or None if the file does not pass the check (then nothing changes)."""
        self._signature = self._stat()
        try:
            wanted = read_setting(self.setting_path)
        except ConfigError as error:
            self.config_error = str(error)
            say(f"ignoring ntp.json: {error}")
            return None
        self.config_error = None
        return wanted

    # -- chronyd --

    def start(self, server: str) -> None:
        write_atomic(self.conf_path, render_config(server, self.run_dir))
        self.server = server
        say(f"starting chronyd with {'the default pool' if not server else 'a server from the dashboard'}")
        self.process = subprocess.Popen([self.chronyd, "-d", "-f", str(self.conf_path)])
        self._started = time.monotonic()
        self._next_start = 0.0

    def stop_chronyd(self) -> None:
        process, self.process = self.process, None
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(STOP_TIMEOUT)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

    def alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    # -- the status the dashboard reads --

    def write_status(self) -> None:
        facts = tracking(self.chronyc, self.run_dir / "chronyd.sock") if self.alive() else {}
        status = {
            "v": 1,
            "at": time.time(),
            "chronyd": self.alive(),
            "server": self.server or DEFAULT_SERVER,
            "synced": facts.get("synced"),
            "stratum": facts.get("stratum"),
            "offset_ms": facts.get("offset_ms"),
            "config_error": self.config_error,
        }
        try:
            write_atomic(self.status_dir / "status.json", json.dumps(status) + "\n")
        except OSError as error:
            say(f"could not write status.json: {error.strerror or type(error).__name__}")

    # -- the loop --

    def run(self) -> int:
        self.start(self.load() or "")  # a setting that fails the check is the default pool, and config_error says why
        self.write_status()
        last_status = time.monotonic()
        while not self.stop.wait(self.poll):
            if self._stat() != self._signature:
                before = (self.server, self.config_error)
                wanted = self.load()
                if wanted is not None and wanted != self.server:
                    self.stop_chronyd()
                    self._failures = 0
                    self.start(wanted)
                    self.write_status()
                    last_status = time.monotonic()
                elif (self.server, self.config_error) != before:
                    self.write_status()
            now = time.monotonic()
            if self.process is not None and self.process.poll() is not None:
                quick = now - self._started < 30  # one that dies at once is failing, not just stopped
                self._failures = self._failures + 1 if quick else 0
                delay = min(self.restart_delay * 2 ** min(self._failures, 5), MAX_RESTART_DELAY)
                say(f"chronyd exited ({self.process.returncode}); starting it again in {delay:.0f} s")
                self.process = None
                self._next_start = now + delay
                self.write_status()
                last_status = now
            if self.process is None and now >= self._next_start:
                self.start(self.server)
            if now - last_status >= self.status_every:
                self.write_status()
                last_status = now
        self.stop_chronyd()
        self.write_status()
        return 0


def say(message: str) -> None:
    print(f"chrony: {message}", flush=True)


def check(status_dir: Path, now: Optional[float] = None) -> int:
    """The health check: the supervisor has written a status lately and chronyd was up in it. Being unable to reach the server
    is not unhealthy: the container is doing its job and the dashboard says what it sees."""
    now = time.time() if now is None else now
    try:
        data = json.loads((status_dir / "status.json").read_text())
        at = float(data["at"])
    except (OSError, ValueError, KeyError, TypeError):
        print("chrony: no status yet", file=sys.stderr)
        return 1
    if abs(now - at) > HEALTH_FRESH_SECONDS:
        print("chrony: the status is old; the supervisor has stopped", file=sys.stderr)
        return 1
    if data.get("chronyd") is not True:
        print("chrony: chronyd is not running", file=sys.stderr)
        return 1
    return 0


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="Runs chronyd for the dashboard's NTP server setting.")
    parser.add_argument("--check", action="store_true", help="the health check: the status is fresh and chronyd is up")
    args = parser.parse_args(argv)
    supervisor = Supervisor()
    if args.check:
        return check(supervisor.status_dir)
    for name in (signal.SIGTERM, signal.SIGINT):
        signal.signal(name, lambda *_: supervisor.stop.set())
    return supervisor.run()


if __name__ == "__main__":
    sys.exit(main())
