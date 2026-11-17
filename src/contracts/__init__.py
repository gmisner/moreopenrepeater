"""Versioned message contracts shared by the core, the API and optional modules.

Stage 2 of the modular migration (#221, #228) defines these as plain data
and test fixtures only. Nothing in production imports this package yet, so it
cannot change core, audio or PTT behavior. See docs/contracts/README.md and
docs/adr/0005-container-module-runtime.md.
"""
from .common import Freshness, Header, Message, Sequencer, Source, StructuredError, new_id, utc_now
from .events import (
    PttCommanded, PttEvidence, PttFeedback, PttReason, TransmissionEnded, TransmissionStarted, TxEndReason,
    core_event_adapter,
)
from .log_event import LogEvent, Severity
from .redaction import OMITTED, REDACTED, redact
from .transmit import IdempotencyLedger, Priority, SpeakDecision, SpeakRequest, SpeakStatus, screen_speak_request
from .versioning import CONTRACT_VERSION, is_compatible

__all__ = [
    "CONTRACT_VERSION",
    "OMITTED",
    "REDACTED",
    "Freshness",
    "Header",
    "IdempotencyLedger",
    "LogEvent",
    "Message",
    "Priority",
    "PttCommanded",
    "PttEvidence",
    "PttFeedback",
    "PttReason",
    "Sequencer",
    "Severity",
    "SpeakDecision",
    "SpeakRequest",
    "SpeakStatus",
    "Source",
    "StructuredError",
    "TransmissionEnded",
    "TransmissionStarted",
    "TxEndReason",
    "core_event_adapter",
    "is_compatible",
    "new_id",
    "redact",
    "screen_speak_request",
    "utc_now",
]
