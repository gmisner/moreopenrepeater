"""systemd's service watchdog: tells systemd the service started, then keeps
telling it the service is alive while the controller and the audio engine
are both making progress.

The unit has `Type=notify` and `WatchdogSec=`; if the pings stop (a hung
event loop, a stuck sound device), systemd kills the service and starts it
again. `Restart=on-failure` alone only helps when the process exits.

The pings come from a thread of their own, so a hung event loop stops them
too. Before giving up, the thread takes the transmitter off the air: a
CM108 keeps its PTT output keyed after the process dies.

A container has no systemd to do the restarting, and Docker does not restart
an unhealthy container. With `MOREOPENREPEATER_EXIT_ON_STALL=1` the same check
runs on its own timer, and on a stall the process records the event, flushes
the log and exits, so the restart policy brings the core back. Without that
the core would sit unhealthy with its transmitter command going nowhere.
"""
from __future__ import annotations

import logging
import os
import socket
import threading
import time
from typing import Callable, Mapping, Optional

from observability import log_event

_logger = logging.getLogger("moreopenrepeater.watchdog")

EXIT_ON_STALL = "MOREOPENREPEATER_EXIT_ON_STALL"
EXIT_STALLED = 75  # EX_TEMPFAIL: the exit status of a core that stopped itself because a part stalled
CONTAINER_CHECK_SECONDS = 2.0  # how often a container checks, with no systemd to say how often
FLUSH_SECONDS = 2.0  # the most the log gets to reach the collector before the process exits

# Longer than any normal pause (the tick runs every 50 ms, audio blocks
# every 20 ms), well inside WatchdogSec.
STALL_SECONDS = 10.0


def sd_notify(message: str, env: Optional[dict] = None) -> bool:
    """Send `message` (e.g. "READY=1") to systemd. False when not run by
    systemd with Type=notify, or if the socket can't be reached."""
    address = (os.environ if env is None else env).get("NOTIFY_SOCKET")
    if not address:
        return False
    if address.startswith("@"):
        address = "\0" + address[1:]  # abstract namespace
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.connect(address)
            sock.sendall(message.encode())
    except OSError as error:
        _logger.warning("couldn't notify systemd (%s): %s", message, error)
        return False
    return True


def exit_on_stall_enabled(env: Optional[Mapping[str, str]] = None) -> bool:
    return (os.environ if env is None else env).get(EXIT_ON_STALL, "").strip().lower() in ("1", "true", "yes", "on")


def exit_for_restart(
    stalled: str,
    *,
    code: int = EXIT_STALLED,
    flush: Callable[[], None] = logging.shutdown,
    flush_seconds: float = FLUSH_SECONDS,
    leave: Callable[[int], None] = os._exit,
) -> None:
    """End the process so its supervisor starts it again. The log is flushed first, so the stall is in the
    record, but never for longer than `flush_seconds`: a hung process must still go. Skips `atexit` and the
    event loop on purpose, since either may be the thing that is stuck."""
    worker = threading.Thread(target=flush, name="watchdog-flush", daemon=True)
    worker.start()
    worker.join(flush_seconds)
    leave(code)


def ping_interval(env: Optional[dict] = None, pid: Optional[int] = None) -> Optional[float]:
    """Seconds between pings, half of systemd's WatchdogSec; None when the
    unit has no watchdog (or set it for another process)."""
    env = os.environ if env is None else env
    usec = env.get("WATCHDOG_USEC", "")
    owner = env.get("WATCHDOG_PID", "")
    if not usec.isdigit() or int(usec) == 0:
        return None
    if owner.isdigit() and int(owner) != (os.getpid() if pid is None else pid):
        return None
    return int(usec) / 1_000_000 / 2


class Watchdog:
    """`watch(name, last_progress)` adds something that has to keep making
    progress: `last_progress()` returns when it last did (on `clock`), or
    None while it isn't running and so can't stall."""

    def __init__(
        self,
        notify: Callable[[str], bool] = sd_notify,
        clock: Callable[[], float] = time.monotonic,
        stall_seconds: float = STALL_SECONDS,
    ) -> None:
        self._notify = notify
        self._clock = clock
        self._stall_seconds = stall_seconds
        self._watched: list[tuple[str, Callable[[], Optional[float]]]] = []
        self.on_stall: Callable[[str], None] = lambda name: None
        #: Called after `on_stall` when something has stalled. In a container this ends the process (see
        #: `exit_for_restart`); under systemd it stays None and the stopped pings do the same job.
        self.restart: Optional[Callable[[str], None]] = None
        self.stalled: Optional[str] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def watch(self, name: str, last_progress: Callable[[], Optional[float]]) -> None:
        self._watched.append((name, last_progress))

    def _first_stalled(self) -> Optional[str]:
        now = self._clock()
        for name, last_progress in self._watched:
            at = last_progress()
            if at is not None and now - at > self._stall_seconds:
                return name
        return None

    def check(self) -> bool:
        """Ping systemd if everything is making progress. Once something
        stalls the pings stop for good, so systemd restarts the service."""
        if self.stalled is None:
            self.stalled = self._first_stalled()
            if self.stalled is not None:
                log_event(
                    _logger, logging.ERROR, "watchdog.stalled",
                    f"the {self.stalled} stopped making progress; restarting the service",
                    error_code="watchdog.stalled", part=self.stalled, stall_s=self._stall_seconds, facts_in_text=False,
                )
                try:
                    self.on_stall(self.stalled)
                except Exception:
                    _logger.exception("couldn't get ready for the restart")
                if self.restart is not None:
                    self.restart(self.stalled)
        if self.stalled is not None:
            return False
        self._notify("WATCHDOG=1")
        return True

    def start(self, interval: float) -> None:
        def run() -> None:
            while not self._stop.wait(interval):
                self.check()

        self._stop.clear()
        self._thread = threading.Thread(target=run, name="watchdog", daemon=True)
        self._thread.start()
        _logger.info("watchdog on: checking every %.0f s", interval)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
