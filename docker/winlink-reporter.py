#!/usr/bin/env python3
"""Sessions and counters for the Winlink page (ADR 0006): runs inside the gateway and modem containers, writes two small files.

The dashboard's API has no Docker access and must not get any, so it cannot read these containers' logs. Instead each container
writes a small JSON snapshot into a volume the API only reads (an in-memory tmpfs: no SD card wear), and keeps nothing else.

  gateway   LinBPQ keeps two logs that are enough, in its own log folder (a tmpfs):
              - BPQ_CMS_ADIF_YYYYMM.adi, one record per session that reached Winlink, written when it ends: the station's
                callsign, when it ended, how long, how many messages each way, their sizes, and how it ended. (ENABLEADIFLOG=1)
              - ConnectLog_YYMMDD.log, one line per accepted connection, so a connection that never reached Winlink shows.
                (LogAllConnects=1)
            Nothing else is opened. LinBPQ's telnet log keeps the password a user typed and its CMS log is a protocol trace,
            so neither is read, and no other file in that folder is.
  modem     runs Dire Wolf as a child, passes its output through unchanged (so `docker compose logs direwolf` is as before),
            and counts two kinds of line by their first characters only: a frame heard, and a frame sent. A line's text is
            matched and dropped, never stored.

What is written is a short whitelist: counts, times, a callsign that looks like one, an outcome word. Nothing a station typed
(its software identification, its locator), no message text, no password. Dire Wolf does not log each time it keys the
transmitter, so there is no count of that: frames sent is the nearest thing it reports.

Standard library only. State is in memory: a restart of the container starts the counts again, and the page says "since".
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

VERSION = 1
MAX_SESSIONS = 100  # the list the page can show; the counters keep counting past it
MAX_COUNT = 10_000_000  # a message count or size read from a log is held to this, so a bad record cannot make a silly number
MAX_SECONDS = 7 * 24 * 3600
GRACE_SECONDS = 30 * 60  # a connection this old with no session after it did not reach Winlink
SCAN_SECONDS = 5.0
HEARTBEAT_SECONDS = 30.0  # rewrite an unchanged snapshot this often, so the page can tell "quiet" from "gone"
CHUNK_BYTES = 256 * 1024
LINE_BYTES = 64 * 1024
MAX_SEEN = 5000

DEFAULT_LOGS = "/run/bpq/logs"
DEFAULT_STATUS_DIR = "/winlink-status"

_CALLSIGN = re.compile(r"^[A-Z0-9]{3,7}(-([0-9]|1[0-5]))?$")
_TAG = re.compile(r"<([A-Za-z_]+):(\d+)(?::[A-Za-z])?>")
_CONNECT = re.compile(r"^(\d{2}):(\d{2}):(\d{2}) Call from (\S+) to (\S+) Mode (\S+)\s*$")
_ADIF_NAME = re.compile(r"^BPQ_CMS_ADIF_\d{6}\.adi$")
_CONNECT_NAME = re.compile(r"^ConnectLog_(\d{2})(\d{2})(\d{2})\.log$")
_HEARD = re.compile(rb"^\[\d+(?:\.\d+){0,2}\] [A-Za-z0-9-]+>")  # [0.3] SRC>DEST: a frame decoded off the air
_SENT = re.compile(rb"^\[\d+[HL]\] [A-Za-z0-9-]+>")  # [0L] SRC>DEST: a frame the modem sent


def _count(text: str) -> Optional[int]:
    """A digits-only field as a non-negative number held to MAX_COUNT, or None if it is not one."""
    if not (text.isascii() and text.isdigit()) or len(text) > 15:
        return None
    return min(int(text), MAX_COUNT)


def _callsign(text: str) -> Optional[str]:
    call = text.strip().upper()
    return call if _CALLSIGN.match(call) else None


def _utc(text: str, form: str) -> Optional[float]:
    try:
        return datetime.strptime(text, form).replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


# -- the session records ------------------------------------------------------------------------------------------------------


def _tail(comment: str) -> Optional[dict]:
    """The numbers at the end of LinBPQ's comment field: ...|termination|sent|received|bytes sent|bytes received|seconds|loc|loc.

    Counted from the end, because the station's own identification sits in the middle and a bar in it would move a count from the
    front; and the locator at the very end can be typed by the station, so a few extra bars there are allowed for. The first
    alignment where all five numbers are digits is the one used."""
    fields = comment.split("|")
    for shift in range(5):
        top = len(fields) - 8 - shift
        if top < 12:
            return None
        numbers = [_count(fields[-3 - shift]), _count(fields[-4 - shift]), _count(fields[-5 - shift]),
                   _count(fields[-6 - shift]), _count(fields[-7 - shift])]
        if None in numbers:
            continue
        seconds, bytes_out, bytes_in, messages_out, messages_in = numbers
        return {"termination": fields[top].strip()[:8], "messages_in": messages_in, "messages_out": messages_out,
                "bytes_in": bytes_in, "bytes_out": bytes_out, "seconds": seconds}
    return None


def _record(fields: dict) -> Optional[dict]:
    comment = fields.get("comment", "")
    parts = comment.split("|")
    comment_end = _utc(parts[1], "%Y-%m-%d %H:%M:%S") if len(parts) > 1 else None
    date, on, off = fields.get("qso_date", ""), fields.get("time_on", ""), fields.get("time_off", "")
    start_tags = _utc(date + on, "%Y%m%d%H%M%S") if len(on) == 6 else None
    end_tags = _utc(date + off, "%Y%m%d%H%M%S") if len(off) == 6 else None
    if start_tags is not None and start_tags < _utc("2000", "%Y"):
        start_tags = None  # LinBPQ writes 1970 when no line ever reached Winlink
    if end_tags is not None and start_tags is not None and end_tags < start_tags:
        end_tags += 86400  # the session crossed midnight
    end = comment_end if comment_end is not None else end_tags
    if end is None:
        return None
    tail = _tail(comment)
    if tail is not None:
        duration = tail["seconds"]
    else:
        duration = int(end - start_tags) if start_tags is not None else 0
    duration = max(0, min(int(duration), MAX_SECONDS))
    end = int(end)
    start = end - duration
    call = _callsign(fields.get("call", ""))
    if tail is None:
        counts, outcome = {"messages_in": 0, "messages_out": 0, "bytes_in": 0, "bytes_out": 0}, "unknown"
    else:
        counts = {key: tail[key] for key in ("messages_in", "messages_out", "bytes_in", "bytes_out")}
        outcome = "completed" if tail["termination"] == "FQ" else "ended_early"
    ident = hashlib.sha1(f"{call}|{start}|{end}".encode()).hexdigest()[:12]
    return {"id": ident, "callsign": call, "start": start, "end": end, "duration_s": duration, **counts, "outcome": outcome}


def parse_adif(text: str) -> tuple[list[dict], int]:
    """Whole session records in `text`, and how much of it to treat as read. A half-written record is left for the next read."""
    lowered = text.lower()
    pos = 0
    header = lowered.find("<eoh>")
    first = lowered.find("<eor>")
    if header != -1 and (first == -1 or header < first):
        pos = header + len("<eoh>")
        while pos < len(text) and text[pos] in "\r\n ":
            pos += 1
    records = []
    consumed = pos
    while True:
        end = lowered.find("<eor>", pos)
        if end == -1:
            break
        fields, cursor, body = {}, 0, text[pos:end]
        while True:
            match = _TAG.search(body, cursor)
            if not match:
                break
            size = int(match.group(2))
            fields[match.group(1).lower()] = body[match.end():match.end() + size]
            cursor = match.end() + size
        record = _record(fields)
        if record is not None:
            records.append(record)
        pos = end + len("<eor>")
        while pos < len(text) and text[pos] in "\r\n ":
            pos += 1
        consumed = pos
    return records, consumed


def parse_connect_line(line: str) -> Optional[int]:
    """Seconds into the UTC day of a connection on the radio (AX.25), or None for any other line or kind of connection."""
    match = _CONNECT.match(line)
    if not match or match.group(6) != "AX.25":
        return None
    hours, minutes, seconds = (int(match.group(n)) for n in (1, 2, 3))
    if hours > 23 or minutes > 59 or seconds > 59:
        return None
    return hours * 3600 + minutes * 60 + seconds


# -- the counters -------------------------------------------------------------------------------------------------------------


class GatewayTracker:
    """The picture of the gateway so far: counters, and the last sessions.

    A connection is matched with the session that follows it (LinBPQ logs the connection when it is accepted and the session when
    it ends), in whichever order the two logs are read. A connection with no session after it within the grace time never reached
    Winlink: no Winlink password, Winlink unreachable, or the station gave up first."""

    def __init__(self, *, grace_seconds: float = GRACE_SECONDS, clock: Callable[[], float] = time.time) -> None:
        self.grace = grace_seconds
        self._clock = clock
        self.started = clock()
        self._sessions: deque = deque(maxlen=MAX_SESSIONS)
        self._seen: dict[str, None] = {}
        self._pending: list[float] = []  # connections not yet followed by a session, oldest first
        self._unmatched: deque = deque(maxlen=200)  # session starts with no connection seen yet
        self.counters = {"sessions": 0, "incomplete": 0, "messages_in": 0, "messages_out": 0, "bytes_in": 0, "bytes_out": 0,
                         "connects": 0, "failed_connects": 0, "last_session_end": None}

    def add_record(self, record: dict) -> bool:
        if record["id"] in self._seen:
            return False
        self._seen[record["id"]] = None
        while len(self._seen) > MAX_SEEN:
            self._seen.pop(next(iter(self._seen)))
        self._sessions.append(record)
        c = self.counters
        c["sessions"] += 1
        if record["outcome"] != "completed":
            c["incomplete"] += 1
        for key in ("messages_in", "messages_out", "bytes_in", "bytes_out"):
            c[key] += record[key]
        if c["last_session_end"] is None or record["end"] > c["last_session_end"]:
            c["last_session_end"] = record["end"]
        candidates = [t for t in self._pending if t <= record["start"] + 5 and record["start"] - t <= self.grace]
        if candidates:
            self._pending.remove(max(candidates))
        else:
            self._unmatched.append(record["start"])
        return True

    def add_connect(self, stamp: float) -> None:
        self.counters["connects"] += 1
        candidates = [s for s in self._unmatched if stamp <= s + 5 and s - stamp <= self.grace]
        if candidates:
            self._unmatched.remove(min(candidates))
            return
        self._pending.append(stamp)
        self._pending.sort()

    def feed_adif(self, text: str) -> None:
        for record in parse_adif(text)[0]:
            self.add_record(record)

    def feed_connects(self, day_epoch: float, text: str) -> None:
        for line in text.splitlines():
            seconds = parse_connect_line(line)
            if seconds is not None:
                self.add_connect(day_epoch + seconds)

    def snapshot(self, now: float) -> dict:
        expired = [t for t in self._pending if t < now - self.grace]
        for stamp in expired:
            self._pending.remove(stamp)
        self.counters["failed_connects"] += len(expired)
        return {"v": VERSION, "kind": "gateway", "updated": now, "started": self.started,
                "counters": dict(self.counters), "sessions": [dict(s) for s in sorted(self._sessions, key=lambda s: s["end"])]}


class ModemCounter:
    """Frames heard and frames sent, from the first characters of Dire Wolf's output lines. The rest of a line is never kept."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self.started = clock()
        self.counters = {"frames_heard": 0, "frames_sent": 0, "last_heard": None, "last_sent": None}

    def feed(self, line: bytes) -> None:
        if _HEARD.match(line):
            self.counters["frames_heard"] += 1
            self.counters["last_heard"] = self._clock()
        elif _SENT.match(line):
            self.counters["frames_sent"] += 1
            self.counters["last_sent"] = self._clock()

    def snapshot(self, now: float) -> dict:
        return {"v": VERSION, "kind": "modem", "updated": now, "started": self.started, "counters": dict(self.counters)}


# -- files --------------------------------------------------------------------------------------------------------------------


def write_snapshot(path, data: dict) -> bool:
    """Replace the file whole (write beside it, then rename), so the dashboard never reads half of one. False if it cannot."""
    path = Path(path)
    try:
        descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".snap-", suffix=".tmp")
    except OSError:
        return False
    try:
        with os.fdopen(descriptor, "w") as handle:
            json.dump(data, handle, separators=(",", ":"))
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
        return True
    except OSError:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        return False


def _read_chunk(path, offset: int, limit: int) -> bytes:
    with open(path, "rb") as handle:
        handle.seek(offset)
        return handle.read(limit)


class GatewayWatcher:
    """Reads the new part of LinBPQ's two log files at each scan and writes the snapshot. Never opens another file."""

    def __init__(self, log_dir, out_path, *, clock: Callable[[], float] = time.time, chunk_bytes: int = CHUNK_BYTES,
                 grace_seconds: float = GRACE_SECONDS) -> None:
        self.log_dir = Path(log_dir)
        self.out_path = Path(out_path)
        self._clock = clock
        self._chunk = chunk_bytes
        self.tracker = GatewayTracker(grace_seconds=grace_seconds, clock=clock)
        self._offsets: dict[str, int] = {}
        self._last_written: Optional[str] = None
        self._last_write_time = 0.0

    def _files(self) -> tuple[list[tuple[float, Path]], list[Path]]:
        connects, sessions = [], []
        try:
            names = sorted(os.listdir(self.log_dir))
        except OSError:
            return connects, sessions
        for name in names:
            match = _CONNECT_NAME.match(name)
            if match:
                try:
                    day = datetime(2000 + int(match.group(1)), int(match.group(2)), int(match.group(3)), tzinfo=timezone.utc)
                except ValueError:
                    continue
                connects.append((day.timestamp(), self.log_dir / name))
            elif _ADIF_NAME.match(name):
                sessions.append(self.log_dir / name)
        return connects, sessions

    def _new_text(self, path: Path, take: Callable[[str], int]) -> str:
        """The unread part of a file, as text, and move past what `take` says was used."""
        key = str(path)
        offset = self._offsets.get(key, 0)
        try:
            if os.path.getsize(path) < offset:
                offset = 0  # replaced by a shorter file: read it again from the start
            data = _read_chunk(path, offset, self._chunk)
        except OSError:
            return ""
        text = data.decode("latin-1")
        used = take(text)
        if used == 0 and len(data) >= self._chunk:
            used = len(data)  # no whole record in a whole chunk: junk, move on
        self._offsets[key] = offset + used
        return text[:used]

    def scan(self) -> None:
        connects, sessions = self._files()
        for day_epoch, path in connects:  # connections first: a session is matched with the connection before it
            def whole_lines(text: str) -> int:
                return text.rfind("\n") + 1
            self.tracker.feed_connects(day_epoch, self._new_text(path, whole_lines))
        for path in sessions:
            holder: list = []

            def records(text: str) -> int:
                found, used = parse_adif(text)
                holder.extend(found)
                return used

            self._new_text(path, records)
            for record in holder:
                self.tracker.add_record(record)
        self._write()

    def _write(self) -> None:
        now = self._clock()
        snapshot = self.tracker.snapshot(now)
        body = json.dumps({**snapshot, "updated": 0}, sort_keys=True)
        if body == self._last_written and now - self._last_write_time < HEARTBEAT_SECONDS:
            return
        if write_snapshot(self.out_path, snapshot):
            self._last_written, self._last_write_time = body, now


# -- the two ways to run --------------------------------------------------------------------------------------------------------


def run_gateway(log_dir, out_path, *, interval: float = SCAN_SECONDS, stop: Optional[threading.Event] = None,
                parent: Optional[int] = None) -> int:
    """Scan and write until told to stop, or until the process that started this (LinBPQ, which the entrypoint execs, so the
    entrypoint's own pid is passed) is gone: then this is adopted by the container's init, and a reporter left running would hold
    the container's output open."""
    stop = stop or threading.Event()
    parent = parent if parent is not None else os.getppid()
    watcher = GatewayWatcher(log_dir, out_path)
    for name in ("SIGTERM", "SIGINT"):
        try:
            signal.signal(getattr(signal, name), lambda *_: stop.set())
        except ValueError:
            pass  # not the main thread (a test)
    failed = False
    while True:
        try:
            watcher.scan()
            failed = False
        except Exception as error:  # noqa: BLE001 - this must keep going: it only reports
            if not failed:
                print(f"winlink-reporter: scan failed ({type(error).__name__}); will try again", file=sys.stderr, flush=True)
            failed = True
        if stop.wait(interval):
            return 0
        if os.getppid() != parent:
            try:
                watcher.scan()  # one last look: the session that ended with it
            except Exception:  # noqa: BLE001
                pass
            return 0


def run_modem(command: list[str], out_path, *, interval: float = SCAN_SECONDS) -> int:
    counter = ModemCounter()
    try:
        child = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except OSError as error:
        print(f"winlink-reporter: cannot start {command[0]}: {error.strerror or type(error).__name__}", file=sys.stderr, flush=True)
        return 127
    for name in ("SIGTERM", "SIGINT"):
        try:
            signal.signal(getattr(signal, name), lambda number, _frame: child.send_signal(number))
        except ValueError:
            pass
    done = threading.Event()
    state = {"last": None, "time": 0.0}

    def writer() -> None:
        while True:
            finished = done.wait(interval)
            body = json.dumps(counter.snapshot(0)["counters"], sort_keys=True)
            now = time.time()
            if body != state["last"] or now - state["time"] >= HEARTBEAT_SECONDS or finished:
                if write_snapshot(out_path, counter.snapshot(now)):
                    state["last"], state["time"] = body, now
            if finished:
                return

    thread = threading.Thread(target=writer, daemon=True)
    thread.start()
    out = sys.stdout.buffer
    at_start = True
    assert child.stdout is not None
    for piece in iter(lambda: child.stdout.readline(LINE_BYTES), b""):
        if at_start:
            counter.feed(piece)
        at_start = piece.endswith(b"\n")
        try:
            out.write(piece)
            out.flush()
        except (OSError, ValueError):
            pass  # nobody is reading the log: the modem still runs
    code = child.wait()
    done.set()
    thread.join(5)
    return code if code >= 0 else 128 - code


def main(argv: Optional[list[str]] = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    command: list[str] = []
    if "--" in arguments:
        split = arguments.index("--")
        arguments, command = arguments[:split], arguments[split + 1:]
    parser = argparse.ArgumentParser(description="Sessions and counters for the Winlink page")
    parser.add_argument("mode", choices=["gateway", "modem"])
    parser.add_argument("--logs", default=os.environ.get("MOR_WINLINK_LOGS", DEFAULT_LOGS))
    parser.add_argument("--out", default=None)
    parser.add_argument("--interval", type=float, default=SCAN_SECONDS)
    parser.add_argument("--parent", type=int, default=None, help="the pid whose end ends this (gateway only)")
    options = parser.parse_args(arguments)
    status_dir = os.environ.get("MOR_WINLINK_STATUS_DIR", DEFAULT_STATUS_DIR)
    out = options.out or os.path.join(status_dir, f"{options.mode}.json")
    if options.mode == "gateway":
        return run_gateway(options.logs, out, interval=options.interval, parent=options.parent)
    if not command:
        parser.error("modem needs the command to run after --")
    return run_modem(command, out, interval=options.interval)


if __name__ == "__main__":
    sys.exit(main())
