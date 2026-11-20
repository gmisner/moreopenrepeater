"""What the core can tell the dashboard about the host it runs on: for now, the kernel's log.

The support bundle wants the USB lines of the kernel log (`usb 1-1-port2: disabled by hub (EMI?)` is the evidence for the
drops that matter most). A container has no `journalctl` or `dmesg`, but the compose file can give the core the host's
`/dev/kmsg`, read-only (docs/docker.md, What the container is allowed to do). This module reads it.

`/dev/kmsg` hands out one record per `read()`: `priority,sequence,microseconds;message`, then zero or more continuation
lines that start with a space (`SUBSYSTEM=usb`, `DEVICE=...`), which are data about the record and not part of it. Opened
non-blocking, a read with nothing left fails with EAGAIN, so the whole ring can be taken in a moment and the reader never
waits for the next message. A reader that fell behind and had records overwritten gets EPIPE once, and the next read
carries on from the oldest record still there.

When the compose file has no host log to give, `/dev/kmsg` inside the container is `/dev/null` (the default of
`MOR_KMSG_DEVICE`). It reads as empty, which would look like a quiet kernel, so it is recognized by its device number and
reported as "not enabled".
"""
from __future__ import annotations

import collections
import errno
import os
import stat
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

KMSG_PATH = Path("/dev/kmsg")
PROC_STAT = Path("/proc/stat")

KERNEL_LINES = 400  # the most lines handed back: the newest
MAX_LINE_CHARS = 400  # a kernel message is far shorter; this keeps one odd record from filling the answer
MAX_RECORDS = 20_000  # the most records looked at; a real ring (128 to 512 KB) holds a few thousand
MAX_BYTES = 4 * 1024 * 1024  # the most bytes read
MAX_SECONDS = 1.0  # the longest the read may take
MAX_OVERWRITES = 8  # EPIPE is expected once; a source that keeps doing it is not worth waiting for
READ_SIZE = 8192  # what the kernel's documentation asks a reader to give `read()`

#: The device number of `/dev/null` on Linux. The host's own `/dev/null` is added where it differs (macOS, for the tests).
_NULL_DEVICES = {os.makedev(1, 3)}
try:
    _NULL_DEVICES.add(os.stat("/dev/null").st_rdev)
except OSError:  # pragma: no cover - no /dev/null at all
    pass

NOT_ENABLED = (
    "the host's kernel log was not given to the core (the compose file maps /dev/kmsg to /dev/null by default); "
    "set MOR_KMSG_DEVICE=/dev/kmsg in docker/.env, then recreate the core, to turn it on"
)


def _read_once(fd: int) -> bytes:
    """One `read()`: one record from `/dev/kmsg`. A seam for the tests, which cannot make the kernel say EPIPE."""
    return os.read(fd, READ_SIZE)


def _is_null_device(info: os.stat_result) -> bool:
    return stat.S_ISCHR(info.st_mode) and info.st_rdev in _NULL_DEVICES


def boot_time(proc_stat: Path = PROC_STAT) -> Optional[float]:
    """When the host booted, in unix seconds, from `btime` in `/proc/stat` (a container sees the host's). None if it
    cannot be read: the lines then carry only the seconds since boot."""
    try:
        for line in proc_stat.read_text().splitlines():
            if line.startswith("btime "):
                return float(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    return None


def format_record(line: bytes, btime: Optional[float]) -> Optional[str]:
    """`[ssss.ssssss] message`, with the wall-clock time in front when `btime` is known; None for a line that is not a
    record (no `;`, no number where the timestamp goes)."""
    text = line.decode("utf-8", errors="replace")
    head, separator, message = text.partition(";")
    fields = head.split(",")
    if not separator or len(fields) < 3:
        return None
    try:
        seconds = int(fields[2]) / 1_000_000
    except ValueError:
        return None
    if seconds < 0:
        return None
    out = f"[{seconds:12.6f}] {message}"
    if btime is not None:
        stamp = datetime.fromtimestamp(btime + seconds, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        out = f"{stamp}Z {out}"
    return out[:MAX_LINE_CHARS]


def read_kernel_log(
    path: Path = KMSG_PATH,
    *,
    lines: int = KERNEL_LINES,
    max_records: int = MAX_RECORDS,
    max_bytes: int = MAX_BYTES,
    max_seconds: float = MAX_SECONDS,
    btime: Optional[float] = None,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[Optional[list[str]], Optional[str]]:
    """The newest `lines` kernel messages, oldest first, or why there are none: `(lines, None)` or `(None, reason)`.

    Never blocks and never raises. It reads until the source says there is nothing more, or a limit is reached (records,
    bytes or time); past a limit the lines are the newest of what was read, which on a source far bigger than the limits is
    not the end of the log. No real kernel ring comes near them."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
    except FileNotFoundError:
        return None, f"{path} is not there: the core was not given the host's kernel log"
    except PermissionError:
        return None, (
            f"permission denied reading {path}: the host may restrict it (kernel.dmesg_restrict=1), "
            "or the core's user cannot read the device"
        )
    except OSError as error:
        return None, f"could not open {path}: {error.strerror or error}"
    try:
        try:
            if _is_null_device(os.fstat(fd)):
                return None, NOT_ENABLED
        except OSError as error:
            return None, f"could not inspect {path}: {error.strerror or error}"
        kept: collections.deque[str] = collections.deque(maxlen=max(1, lines))
        records = size = overwritten = 0
        pending = b""  # the start of a line whose end has not been read: never from the kernel (a record is one whole read), possible from a file or pipe
        deadline = clock() + max_seconds

        def take(line: bytes) -> None:
            nonlocal records
            if line.startswith(b" "):
                return
            records += 1
            text = format_record(line, btime)
            if text is not None:
                kept.append(text)

        while records < max_records and size < max_bytes and clock() < deadline:
            try:
                chunk = _read_once(fd)
            except BlockingIOError:  # nothing more: the whole ring has been read
                break
            except OSError as error:
                if error.errno == errno.EPIPE and overwritten < MAX_OVERWRITES:  # records were lost; the next read goes on
                    overwritten += 1
                    pending = b""
                    continue
                if kept:
                    break
                return None, f"could not read {path}: {error.strerror or error}"
            if not chunk:  # end of file: a plain file, or a pipe nobody writes to
                break
            size += len(chunk)
            *complete, pending = (pending + chunk).split(b"\n")
            for line in complete:
                if records >= max_records:
                    break
                take(line)
        else:
            pending = b""  # a limit was reached: a half line at the edge is not worth keeping
        if pending:
            take(pending)
        return list(kept), None
    finally:
        os.close(fd)
