"""Turn ordinary log records into structured events (#221, #229).

The project logs through the standard library, and a great deal of code already does. This does not ask
anyone to rewrite that: a plain `logger.info("...")` becomes a `legacy.<area>` event carrying its text, and
code that has something worth counting or searching says so with `log_event`, which gives it a stable dotted
name and a context of facts (a number, a state, a node) instead of a sentence.

A record is first reduced to a `RecordSnapshot` in the thread that logged, which is cheap. Building the
event (validation, redaction, JSON) is a separate step that handlers run wherever suits them.
"""
from __future__ import annotations

import contextlib
import logging
import re
import threading
import time
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterator, Optional

from contracts import LogEvent, Sequencer, Severity
from contracts.common import CODE_PATTERN, ID_PATTERN
from contracts.redaction import redact

EXTRA_KEY = "mor_event"  # where `log_event` puts its fields on the record
MAX_MESSAGE = 200
MAX_EVENT_NAME = 80
_NAME = re.compile(CODE_PATTERN)
_ID = re.compile(ID_PATTERN)

_correlation: ContextVar[Optional[str]] = ContextVar("mor_correlation_id", default=None)
_cycle_lock = threading.Lock()
_cycle: Optional[str] = None  # the activity cycle in progress in this process, if any
_lingering: Optional[tuple[str, float]] = None  # a cycle that just ended, and until when it still claims events
_monotonic = time.monotonic
CYCLE_GRACE_SECONDS = 2.0  # the transmitter is unkeyed a moment after the controller says idle


@dataclass
class ProcessIdentity:
    """Who this process is, and its counter: `boot_id` changes on every restart so a collector can tell a
    restart from lost events."""

    module: str
    version: str
    sequencer: Sequencer = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.sequencer is None:
            self.sequencer = Sequencer()


@dataclass(frozen=True)
class RecordSnapshot:
    created: float
    levelno: int
    name: str
    message: str
    structured: Optional[dict]
    exception: Optional[str]
    correlation_id: Optional[str]


def stamp(record: logging.LogRecord, identity: ProcessIdentity) -> int:
    """The sequence number for a record, drawn once: a record that goes to stdout and to the collector is one
    event, so it must carry one number to both."""
    number = getattr(record, "_mor_seq", None)
    if number is None:
        number = identity.sequencer.next()
        record._mor_seq = number  # type: ignore[attr-defined]
    return number


def snapshot(record: logging.LogRecord) -> RecordSnapshot:
    """What an event needs from a record, taken in the logging thread: formatted text, the structured fields
    if there are any, the exception's type (never its text), and the correlation id in force."""
    try:
        message = record.getMessage()
    except Exception:  # noqa: BLE001 - a bad format string must not break logging
        message = str(record.msg)
    exception = record.exc_info[0].__name__ if record.exc_info and record.exc_info[0] else None
    return RecordSnapshot(
        created=record.created, levelno=record.levelno, name=record.name, message=message,
        structured=getattr(record, EXTRA_KEY, None), exception=exception,
        correlation_id=_correlation.get() or _stamped_cycle(),
    )


def severity_for(levelno: int) -> Severity:
    for severity in (Severity.CRITICAL, Severity.ERROR, Severity.WARNING, Severity.INFO):
        if levelno >= severity:
            return severity
    return Severity.DEBUG


def _legacy_name(logger_name: str) -> str:
    suffix = logger_name.split(".", 1)[1] if logger_name.startswith("moreopenrepeater.") else logger_name
    if logger_name == "moreopenrepeater" or not suffix:
        suffix = "root"
    suffix = re.sub(r"[^a-z0-9_]", "_", suffix.lower()).strip("_") or "root"
    if not suffix[0].isalpha():
        suffix = "x" + suffix
    return ("legacy." + suffix)[:MAX_EVENT_NAME]


def _clean_message(text: str) -> str:
    return redact({"m": text})["m"][:MAX_MESSAGE]  # masks bearer tokens and user:pass@host


def record_to_event(snap: RecordSnapshot, identity: ProcessIdentity, seq: int) -> LogEvent:
    """The event for a record. Never raises: a record that cannot be converted becomes a warning that says so."""
    common: dict[str, Any] = {
        "boot_id": identity.sequencer.boot_id, "seq": seq, "module": identity.module,
        "module_version": identity.version, "severity": severity_for(snap.levelno),
    }
    try:
        when = datetime.fromtimestamp(snap.created, timezone.utc)
        structured = snap.structured
        if structured is not None:
            return LogEvent(
                time=when, event=structured["event"], message=_clean_message(structured["message"]) if structured.get("message") else None,
                error_code=structured.get("error_code"), context=structured.get("context") or {},
                correlation_id=structured.get("correlation_id") or snap.correlation_id, **common,
            )
        context: dict[str, Any] = {"logger": snap.name}
        error_code = None
        if snap.exception:
            error_code, context["exception"] = "legacy.exception", snap.exception
        return LogEvent(
            time=when, event=_legacy_name(snap.name), message=_clean_message(snap.message), error_code=error_code,
            context=context, correlation_id=snap.correlation_id, **common,
        )
    except Exception:  # noqa: BLE001
        common["severity"] = Severity.WARNING
        return LogEvent(
            time=datetime.now(timezone.utc), event="log.unconvertible", context={"logger": str(snap.name)[:60]}, **common
        )


def log_event(
    logger: logging.Logger,
    severity: int,
    event: str,
    message: Optional[str] = None,
    *,
    error_code: Optional[str] = None,
    correlation_id: Optional[str] = None,
    facts_in_text: bool = True,
    **context: Any,
) -> None:
    """Log a named event with facts. `event` is a stable dotted lowercase name (`audio.glitch`); put what
    varies in `context`, not in the name or the message, so events can be counted and filtered. Context is
    redacted when the event is built, and the text line written to the log file shows only the redacted form.
    `facts_in_text=False` keeps the text line to the message alone, for a message that already says it all
    in words (the event still carries the facts)."""
    level = int(severity)
    if not logger.isEnabledFor(level):
        return
    if not (_NAME.match(event) and len(event) <= MAX_EVENT_NAME):
        context = {**context, "name": str(event)[:MAX_EVENT_NAME]}
        event = "log.bad_event_name"
    if correlation_id is not None and not _ID.match(correlation_id):
        context, correlation_id = {**context, "correlation_id": str(correlation_id)[:64]}, None
    shown = redact(context)
    text = message or event
    if shown and facts_in_text:
        text += " " + " ".join(f"{key}={value}" for key, value in shown.items())
    logger.log(
        level, "%s", text[:MAX_MESSAGE * 2], stacklevel=2,
        extra={EXTRA_KEY: {
            "event": event, "message": message, "error_code": error_code,
            "correlation_id": correlation_id, "context": context,
        }},
    )


def begin_cycle() -> str:
    """Start an activity cycle (the repeater leaving idle) and return its id. Until `end_cycle`, every event
    logged by any thread of this process carries it, so one transmission can be followed from the controller
    to the audio thread. A cycle that is still open is replaced: a missed `end_cycle` must not stick."""
    global _cycle
    from contracts import new_id

    with _cycle_lock:
        _cycle = new_id()  # an open cycle takes precedence over one that is only lingering
        return _cycle


def end_cycle(grace_seconds: float = 0.0) -> None:
    """The cycle is over. With a grace, events logged in the next `grace_seconds` still carry its id unless a
    new cycle begins first: the controller goes idle just before the transmitter is actually unkeyed, and
    that unkey belongs to the cycle it ends."""
    global _cycle, _lingering
    with _cycle_lock:
        _lingering = (_cycle, _monotonic() + grace_seconds) if _cycle and grace_seconds > 0 else None
        _cycle = None


def current_cycle() -> Optional[str]:
    """The cycle in progress, not one that is only lingering."""
    return _cycle


def _stamped_cycle() -> Optional[str]:
    cycle = _cycle
    if cycle:
        return cycle
    lingering = _lingering
    if lingering is not None and _monotonic() < lingering[1]:
        return lingering[0]
    return None


@contextlib.contextmanager
def correlation(correlation_id: Optional[str] = None) -> Iterator[str]:
    """Everything logged inside this block, by any code, carries this correlation id (a transmission, a
    command), so one thing that happened can be followed across modules."""
    from contracts import new_id

    current = correlation_id or new_id()
    token = _correlation.set(current)
    try:
        yield current
    finally:
        _correlation.reset(token)
