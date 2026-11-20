"""Is the Pi's clock right? Check it against an NTP server, and say whether the optional time container is running.

The repeater leans on the clock: station ID times, the spoken time, scheduled announcements and nets, and every time stamp in
the logs and the activity history. A Pi has no battery clock, so one that boots offline can be hours or days off.

What lives here:

- The NTP server setting: a host name or an IP address in a small file of its own (`ntp.json`), not in the repeater's
  configuration. The optional `chrony` container reads that same file and sets the clock from it (docs/docker.md, The time
  container). The value is written into chrony's configuration over there, so it is validated very strictly, here and again
  by the container.
- A minimal SNTP client (RFC 4330, standard library only): one UDP packet to port 123, a short timeout, the offset and the
  round trip. A reply only counts if it comes from the address asked, answers the question we sent (the originate timestamp is
  echoed back), is a server reply from a server that says it is synchronized, and has sane timestamps. A bogus reply cannot make
  a wrong clock look right, and a kiss-o'-death (the server telling us to go away) is never read as a measurement.
- The check never blocks anything. It runs in a worker thread, at most one at a time, with a deadline, and its answer is cached
  for a minute, so a page that polls cannot hammer the server.
- A gentle background check that raises an alert when the clock is far off, and one when it is right again.
- What the time container wrote about itself (`status.json`), to say whether it is running.

Logging is a stable event name and numbers. Never the server's name.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
import re
import secrets
import socket
import struct
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Optional

from observability import log_event

from .notify import Alert

_logger = logging.getLogger("moreopenrepeater.time")

DEFAULT_SERVER = "pool.ntp.org"  # what is checked, and what the container uses, while the setting is empty
MAX_SERVER_LENGTH = 253  # the longest a DNS name can be
NTP_PORT = 123
QUERY_TIMEOUT_SECONDS = 2.0  # one exchange with one address
MAX_ADDRESSES = 2  # a name can have many addresses; try this many, so one dead one is not "unreachable"
CHECK_DEADLINE_SECONDS = 8.0  # the whole check, name lookup included. A lookup that hangs is abandoned, not waited for
CACHE_SECONDS = 60.0
OFFSET_LIMIT_SECONDS = 2.0  # off by this much or more: the state is "off", and (twice running) an alert goes out
OFFSET_CLEAR_SECONDS = 1.0  # the alert clears once the clock is within this: so one that hovers at the limit does not flap
ALERT_CONFIRM_CHECKS = 2  # in a row: one odd reading (a loaded Pi, a slow reply) is not an alert
ALERT_REPEAT_SECONDS = 6 * 3600  # still wrong: say so again after this long, not on every check
FIRST_CHECK_SECONDS = 120.0  # after start, so a container that is about to fix the clock gets the chance first
CHECK_SECONDS = 10 * 60.0  # then every ten minutes
STATUS_FRESH_SECONDS = 120.0  # the container writes every 30 s; a file older than this means it is not running
MAX_STATUS_BYTES = 4096

NTP_UNIX_EPOCH = 2208988800  # seconds between 1900-01-01 and 1970-01-01
PACKET_SIZE = 48
_FRACTION = 2 ** 32

STATE_OK = "ok"
STATE_OFF = "off"
STATE_UNREACHABLE = "unreachable"
STATE_NOT_CHECKED = "not_checked"

# What a failed check says, in the log (the code) and to a person (the sentence).
ERROR_TEXT = {
    "resolve": "The name could not be looked up. Check the spelling, and that this machine can reach a DNS server.",
    "timeout": "No answer within a couple of seconds. The server may be down, or UDP port 123 may be blocked.",
    "network": "The network refused the request (no route to the server).",
    "kiss": "The server asked us to stop (a kiss-o'-death reply). It may be limiting how often it answers.",
    "unsynchronized": "The server answered but says its own clock is not set, so it was not used.",
    "bad_reply": "The server's answer did not make sense, so it was not used.",
}


# -- the setting -----------------------------------------------------------------------------------------------------


class TimeServerError(ValueError):
    """The NTP server value is not one that may be written into chrony's configuration."""


_NAME_CHARS = re.compile(r"[A-Za-z0-9.:-]+")  # a name, an IPv4 address or an IPv6 address: nothing else gets further
_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")
_IPV4_SHAPE = re.compile(r"[0-9]+(?:\.[0-9]+){3}")


def check_server(value: object) -> str:
    """The value, if it is safe: empty (meaning the default), an IP address, or a DNS name. Otherwise TimeServerError.

    Strict on purpose, because the container writes this into chrony's configuration file, one directive per line: letters,
    digits, dots and hyphens in labels (never starting or ending with a hyphen), or an IPv6 address. No whitespace, no quotes,
    no `#` or `;`, no `/`, no `%` (so no IPv6 zone), no non-ASCII (an internationalized name is given in its xn-- form), and
    nothing that starts with a dash (it would be read as an option). An address that cannot be a time server (this machine,
    the unspecified address, a multicast or reserved one) is refused too."""
    if not isinstance(value, str):
        raise TimeServerError("The NTP server must be text.")
    if value == "":
        return ""
    if len(value) > MAX_SERVER_LENGTH:
        raise TimeServerError(f"The NTP server name is too long (at most {MAX_SERVER_LENGTH} characters).")
    if not _NAME_CHARS.fullmatch(value):  # ASCII letters, digits, dot, colon and hyphen: the class has nothing else in it
        raise TimeServerError(
            "Use a host name or an IP address: letters, digits, dots and hyphens only, no spaces or other symbols."
        )
    if ":" in value:
        try:
            address = ipaddress.IPv6Address(value)
        except ValueError:
            raise TimeServerError("That is not a valid IPv6 address.") from None
        return _refuse_unusable(address, value)
    if _IPV4_SHAPE.fullmatch(value):
        try:
            address = ipaddress.IPv4Address(value)
        except ValueError:
            raise TimeServerError("That is not a valid IPv4 address.") from None
        return _refuse_unusable(address, value)
    labels = value.split(".")
    if not all(_LABEL.fullmatch(label) for label in labels):
        raise TimeServerError("That is not a valid host name (empty parts, or a part that starts or ends with a hyphen).")
    if labels[-1].isdigit():
        raise TimeServerError("A host name does not end in a number. If that is meant as an IP address, it is not a valid one.")
    if value.lower() == "localhost":
        raise TimeServerError("That is this machine itself. Give the name or address of a time server.")
    return value


def _refuse_unusable(address, value: str) -> str:
    if address.is_unspecified or address.is_multicast or address.is_loopback or address.is_reserved:
        raise TimeServerError("That address cannot be a time server (this machine, unspecified, multicast or reserved).")
    return value


def _write_atomic(path: Path, data: bytes, mode: int) -> None:
    """The old file or the new one, never half of one: write beside it, flush to disk, rename over it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_name, mode)
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    try:  # make the rename itself durable (a power cut right after is common on a Pi)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError:
        pass


class NtpSetting:
    """The NTP server, in `ntp.json` in the time-config folder. `directory=None` keeps it in memory (tests).

    The file is readable by everyone (0644): the time container runs as another user and only reads it. It holds nothing secret."""

    FILE_NAME = "ntp.json"

    def __init__(self, directory: Optional[Path]) -> None:
        self.path = None if directory is None else Path(directory) / self.FILE_NAME
        self._memory = ""

    def get(self) -> str:
        """What is set, or empty (the default). A missing, unreadable or invalid file is the default, never an error."""
        if self.path is None:
            return self._memory
        try:
            if self.path.stat().st_size > MAX_STATUS_BYTES:
                return ""
            data = json.loads(self.path.read_text())
            return check_server(data["server"]) if isinstance(data, dict) else ""
        except (OSError, ValueError, KeyError, TypeError):
            return ""

    def set(self, server: str) -> None:
        server = check_server(server)
        if self.path is None:
            self._memory = server
            return
        _write_atomic(self.path, (json.dumps({"server": server}) + "\n").encode(), 0o644)

    @property
    def effective(self) -> str:
        return self.get() or DEFAULT_SERVER


# -- the SNTP client -------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SntpResult:
    """`offset` is the server's time minus this machine's, in seconds: positive means this clock is behind. `delay` is the
    round trip. `error` is a code from ERROR_TEXT when `ok` is false."""

    ok: bool
    offset: Optional[float] = None
    delay: Optional[float] = None
    stratum: Optional[int] = None
    error: Optional[str] = None


def _to_ntp(unix_time: float) -> tuple[int, int]:
    whole = int(unix_time)
    return (whole + NTP_UNIX_EPOCH) & 0xFFFFFFFF, int((unix_time - whole) * _FRACTION) & 0xFFFFFFFF


def _from_ntp(seconds: int, fraction: int, near: float) -> float:
    """A timestamp as Unix time, in the 136-year era that puts it closest to `near` (so 2036 is not a cliff)."""
    base = int(near) + NTP_UNIX_EPOCH
    difference = ((seconds - base + 2 ** 31) % 2 ** 32) - 2 ** 31
    return int(near) + difference + fraction / _FRACTION


def _addresses(host: str, port: int, resolver) -> list[tuple[int, tuple]]:
    """(family, address) to try, IPv4 first (a container network often has no IPv6), at most MAX_ADDRESSES."""
    found = resolver(host, port, type=socket.SOCK_DGRAM)
    ordered = sorted(found, key=lambda entry: entry[0] != socket.AF_INET)
    seen: list[tuple[int, tuple]] = []
    for family, _kind, _proto, _name, address in ordered:
        if (family, address) not in seen:
            seen.append((family, address))
    return seen[:MAX_ADDRESSES]


def sntp_query(
    host: str,
    *,
    port: int = NTP_PORT,
    timeout: float = QUERY_TIMEOUT_SECONDS,
    wall: Callable[[], float] = time.time,
    resolver=socket.getaddrinfo,
) -> SntpResult:
    """Ask `host` what time it is, once (RFC 4330). Never raises. Blocks for at most about `timeout` per address, plus the
    name lookup, so call it from a worker thread."""
    try:
        targets = _addresses(host, port, resolver)
    except (OSError, UnicodeError):
        return SntpResult(False, error="resolve")
    if not targets:
        return SntpResult(False, error="resolve")
    last = SntpResult(False, error="timeout")
    for family, address in targets:
        result = _exchange(family, address, timeout, wall)
        if result.ok or result.error in ("kiss", "unsynchronized"):
            return result  # an answer from the server itself: asking its other addresses would not change it
        last = result
    return last


def _exchange(family: int, address: tuple, timeout: float, wall: Callable[[], float]) -> SntpResult:
    try:
        sock = socket.socket(family, socket.SOCK_DGRAM)
    except OSError:
        return SntpResult(False, error="network")
    try:
        expected = ipaddress.ip_address(address[0])
        t1 = wall()
        sent_seconds, sent_fraction = _to_ntp(t1)
        sent_fraction = (sent_fraction & 0xFFFF0000) | secrets.randbits(16)  # below what this clock can tell apart (15 microseconds): an off-path guess has to match it
        packet = struct.pack("!4B11I", 0x23, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, sent_seconds, sent_fraction)  # leap 0, version 4, mode 3 (client)
        deadline = time.monotonic() + timeout
        try:
            sock.sendto(packet, address)
        except OSError:
            return SntpResult(False, error="network")
        ignored = False
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            sock.settimeout(remaining)
            try:
                data, source = sock.recvfrom(1024)
            except socket.timeout:
                break
            except OSError:
                return SntpResult(False, error="network")
            t4 = wall()
            try:
                same_source = ipaddress.ip_address(source[0]) == expected and source[1] == address[1]
            except ValueError:
                same_source = False
            if not same_source:
                ignored = True  # not from the server we asked: not ours to read, and not a reason to stop waiting for the real one
                continue
            outcome = _read_reply(data, (sent_seconds, sent_fraction), t1, t4)
            if outcome is None:
                ignored = True
                continue
            return outcome
        return SntpResult(False, error="bad_reply" if ignored else "timeout")
    finally:
        sock.close()


def _read_reply(data: bytes, sent: tuple[int, int], t1: float, t4: float) -> Optional[SntpResult]:
    """The result for one datagram from the right address, or None if it is not an answer to our question (keep waiting)."""
    if len(data) < PACKET_SIZE:
        return None
    first, stratum, _poll, _precision, _delay, _dispersion, _ref, *stamps = struct.unpack("!4B11I", data[:PACKET_SIZE])
    leap, version, mode = first >> 6, (first >> 3) & 7, first & 7
    if mode != 4 or version not in (3, 4):
        return None
    originate = (stamps[2], stamps[3])
    if originate != sent:
        return None  # an answer to some other question: stale, or made up by someone who did not see ours
    # From here on it is the server answering us. What it says decides, whatever it says.
    if stratum == 0:
        return SntpResult(False, stratum=0, error="kiss")  # RATE, DENY, RSTR...: a request to stop, not a time
    if leap == 3 or stratum >= 16:
        return SntpResult(False, stratum=stratum, error="unsynchronized")
    t2 = _from_ntp(stamps[4], stamps[5], t1)
    t3 = _from_ntp(stamps[6], stamps[7], t1)
    if (stamps[4], stamps[5]) == (0, 0) or (stamps[6], stamps[7]) == (0, 0) or t3 < t2:
        return SntpResult(False, stratum=stratum, error="bad_reply")
    delay = (t4 - t1) - (t3 - t2)
    if delay < -0.01 or t4 < t1:  # a reply cannot arrive before it was asked for: our clock moved, or the server is making this up
        return SntpResult(False, stratum=stratum, error="bad_reply")
    offset = ((t2 - t1) + (t3 - t4)) / 2
    return SntpResult(True, offset=offset, delay=max(delay, 0.0), stratum=stratum)


# -- the check, cached ---------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ClockReading:
    """One check, kept for CACHE_SECONDS."""

    server: str  # the host that was asked
    at: float  # wall clock time the check finished
    result: SntpResult

    @property
    def state(self) -> str:
        if not self.result.ok:
            return STATE_UNREACHABLE
        return STATE_OFF if abs(self.result.offset or 0.0) >= OFFSET_LIMIT_SECONDS else STATE_OK


class ClockCheck:
    """The cached, never-blocking answer to "is this clock right?". `query=None` is a check that is switched off (tests)."""

    def __init__(
        self,
        setting: NtpSetting,
        query: Optional[Callable[[str], SntpResult]] = sntp_query,
        *,
        cache_seconds: float = CACHE_SECONDS,
        deadline: float = CHECK_DEADLINE_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
    ) -> None:
        self.setting = setting
        self._query = query
        self._cache_seconds = cache_seconds
        self._deadline = deadline
        self._clock = clock
        self._wall = wall
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="time-check")
        self._cached: Optional[tuple[float, ClockReading]] = None
        self._running: Optional[asyncio.Future] = None

    @property
    def enabled(self) -> bool:
        return self._query is not None

    async def current(self) -> Optional[ClockReading]:
        if self._query is None:
            return None
        server = self.setting.effective
        cached = self._cached
        if cached is not None and cached[1].server == server and self._clock() - cached[0] < self._cache_seconds:
            return cached[1]
        if self._running is None or self._running.done():
            self._running = asyncio.ensure_future(self._refresh(server))
        # Shielded: a client that hangs up must not cancel the check other callers are waiting for.
        return await asyncio.shield(self._running)

    async def _refresh(self, server: str) -> ClockReading:
        loop = asyncio.get_running_loop()
        query = self._query
        assert query is not None
        try:
            result = await asyncio.wait_for(loop.run_in_executor(self._executor, query, server), self._deadline)
        except asyncio.TimeoutError:
            result = SntpResult(False, error="timeout")  # the worker may still be in a name lookup; it is left to give up on its own
        except Exception:  # noqa: BLE001 - a check must never raise into the dashboard
            result = SntpResult(False, error="network")
        reading = ClockReading(server=server, at=self._wall(), result=result)
        if server == self.setting.effective:  # not if the setting changed while it ran
            self._cached = (self._clock(), reading)
        return reading


# -- what the time container says about itself -------------------------------------------------------------------------------


def read_container_status(directory: Optional[Path], now: float) -> dict:
    """The time container's `status.json`, as the dashboard shows it. Never raises. `running` needs a file that is fresh and says
    chronyd is up: a missing or old file is a container that is not running (or not installed)."""
    empty = {"running": False, "synced": None, "stratum": None, "offset_ms": None, "server": None, "config_error": None, "updated_at": None}
    if directory is None:
        return empty
    path = Path(directory) / "status.json"
    try:
        if path.stat().st_size > MAX_STATUS_BYTES:
            return empty
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return empty
    if not isinstance(data, dict):
        return empty
    at = data.get("at")
    if isinstance(at, bool) or not isinstance(at, (int, float)):
        return empty
    fresh = abs(now - float(at)) <= STATUS_FRESH_SECONDS

    def number(name: str) -> Optional[float]:
        value = data.get(name)
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None

    def text(name: str) -> Optional[str]:
        value = data.get(name)
        return value[:200] if isinstance(value, str) else None

    stratum = number("stratum")
    synced = data.get("synced")
    return {
        "running": bool(fresh and data.get("chronyd") is True),
        "synced": synced if isinstance(synced, bool) else None,
        "stratum": int(stratum) if stratum is not None else None,
        "offset_ms": number("offset_ms"),
        "server": text("server"),
        "config_error": text("config_error"),
        "updated_at": float(at),
    }


# -- alerts ---------------------------------------------------------------------------------------------------------------------

WHAT_A_WRONG_CLOCK_BREAKS = (
    "A wrong clock throws off the station ID times (the ID goes out by the clock, so the interval and the time of day are wrong), "
    "the spoken time, scheduled announcements and nets, and the time stamps in the logs and the activity history."
)


class ClockAlerts:
    """Turns readings into alerts: once when the clock has been off twice running, again (not more than every few hours) while it
    stays off, and once when it is back. A server that cannot be reached says nothing about the clock, so it changes nothing."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._off = False
        self._bad_checks = 0
        self._alerted_at = 0.0

    def observe(self, reading: Optional[ClockReading], *, container_running: bool = False) -> list[Alert]:
        if reading is None or not reading.result.ok:
            return []
        offset = reading.result.offset or 0.0
        now = self._clock()
        if abs(offset) >= OFFSET_LIMIT_SECONDS:
            self._bad_checks += 1
            if not self._off and self._bad_checks >= ALERT_CONFIRM_CHECKS:
                self._off, self._alerted_at = True, now
                return [self._wrong(offset, container_running)]
            if self._off and now - self._alerted_at >= ALERT_REPEAT_SECONDS:
                self._alerted_at = now
                return [self._wrong(offset, container_running)]
            return []
        if abs(offset) < OFFSET_CLEAR_SECONDS:
            self._bad_checks = 0
            if self._off:
                self._off = False
                return [Alert(
                    "clock", "Clock is correct again",
                    f"The Pi's clock is within {abs(offset):.1f} seconds of the time server again.", "info",
                )]
        elif not self._off:
            self._bad_checks = 0  # between the limits and never confirmed: not a streak
        return []

    def _wrong(self, offset: float, container_running: bool) -> Alert:
        how = "behind" if offset > 0 else "ahead of"
        fix = (
            "The time container is running and should correct it by itself within a few minutes."
            if container_running
            else "Turn on the time container (./setup.sh --time in the docker folder), or set the clock on the Pi (timedatectl set-ntp true)."
        )
        return Alert(
            "clock", "The Pi's clock is wrong",
            f"The Pi's clock is {abs(offset):.0f} seconds {how} the time server. {WHAT_A_WRONG_CLOCK_BREAKS} {fix}",
            "warning",
        )


# -- the whole of it ------------------------------------------------------------------------------------------------------------


class TimeSync:
    """What the routes and the background check use."""

    def __init__(
        self,
        setting: NtpSetting,
        check: ClockCheck,
        status_dir: Optional[Path] = None,
        *,
        alerts: Optional[ClockAlerts] = None,
        wall: Callable[[], float] = time.time,
    ) -> None:
        self.setting = setting
        self._check = check
        self._status_dir = status_dir
        self._alerts = alerts or ClockAlerts(wall)
        self._wall = wall
        self._last_signature: Optional[tuple] = None

    @classmethod
    def offline(cls) -> "TimeSync":
        """In memory, and it never touches the network: what an app built without real settings gets (tests)."""
        setting = NtpSetting(None)
        return cls(setting, ClockCheck(setting, None))

    @classmethod
    def from_env(cls, env: Mapping[str, str], default_dir: Path) -> "TimeSync":
        directory = Path(env.get("MOREOPENREPEATER_TIME_CONFIG_DIR") or default_dir)
        status_dir = Path(env.get("MOREOPENREPEATER_TIME_STATUS_DIR") or directory)
        setting = NtpSetting(directory)
        return cls(setting, ClockCheck(setting), status_dir)

    async def status(self) -> dict:
        reading = await self._check.current()
        return self._describe(reading)

    async def set_server(self, server: str) -> dict:
        """Change the server (TimeServerError if it is not acceptable) and check against the new one."""
        self.setting.set(server)
        log_event(
            _logger, logging.INFO, "time.server_changed", "The NTP server was changed",
            default=not self.setting.get(), facts_in_text=False,
        )
        return await self.status()

    async def poll(self) -> list[Alert]:
        """For the background loop: check (cached), note a change, and return the alerts to send."""
        reading = await self._check.current()
        self._note(reading)
        running = read_container_status(self._status_dir, self._wall())["running"]
        return self._alerts.observe(reading, container_running=running)

    def _describe(self, reading: Optional[ClockReading]) -> dict:
        configured = self.setting.get()
        sntp = reading.result if reading is not None else None
        ok = bool(sntp and sntp.ok)
        return {
            "server": self.setting.effective,
            "configured_server": configured,
            "default_server": DEFAULT_SERVER,
            "state": reading.state if reading is not None else STATE_NOT_CHECKED,
            "reachable": ok if reading is not None else None,
            "offset_ms": round(sntp.offset * 1000, 1) if ok and sntp.offset is not None else None,
            "round_trip_ms": round(sntp.delay * 1000, 1) if ok and sntp.delay is not None else None,
            "stratum": sntp.stratum if ok else None,
            "limit_ms": int(OFFSET_LIMIT_SECONDS * 1000),
            "checked_at": reading.at if reading is not None else None,
            "error": sntp.error if sntp is not None and not sntp.ok else None,
            "error_text": ERROR_TEXT.get(sntp.error or "") if sntp is not None and not sntp.ok else None,
            "container": read_container_status(self._status_dir, self._wall()),
        }

    def _note(self, reading: Optional[ClockReading]) -> None:
        """Say so when the picture changes, not on every check."""
        if reading is None:
            return
        signature = (reading.state, reading.result.error)
        previous, self._last_signature = self._last_signature, signature
        if signature == previous:
            return
        result = reading.result
        if reading.state == STATE_UNREACHABLE:
            log_event(
                _logger, logging.WARNING, "time.check_failed", "The clock check could not get an answer",
                reason=result.error, facts_in_text=False,
            )
        elif reading.state == STATE_OFF:
            log_event(
                _logger, logging.WARNING, "time.clock_off", "The clock is off",
                offset_ms=round((result.offset or 0.0) * 1000), limit_ms=int(OFFSET_LIMIT_SECONDS * 1000), facts_in_text=False,
            )
        elif previous is not None:
            log_event(
                _logger, logging.INFO, "time.clock_ok", "The clock is right",
                offset_ms=round((result.offset or 0.0) * 1000), facts_in_text=False,
            )
