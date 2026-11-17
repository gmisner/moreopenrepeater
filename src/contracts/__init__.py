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
from .commands import PROGRAMMING, SIMULATED, CommandName, CommandResult, CommandStatus, CoreCommand
from .configuration import ConfigApplied, ConfigChange, ConfigResult, ConfigSnapshot, ConfigState, LinkEvents, ProgrammingState
from .health import HealthCheck, HealthReport, HealthState
from .emit import DeliveryError, Emitter, ListSink, StdoutSink, UnixDatagramSink
from .log_event import LogEvent, Severity
from .metrics import Metrics
from .manifest import (
    DeviceAccess, DeviceNeed, ModuleAnnounce, ModuleManifest, ResourceBudget, SafeState, find_device_conflicts,
)
from .redaction import OMITTED, REDACTED, find_sensitive_keys, redact
from .status import CoreStatus, FixQuality, LocationSource, SiteLocation
from .transmit import IdempotencyLedger, Priority, SpeakDecision, SpeakRequest, SpeakStatus, screen_speak_request
from .versioning import CONTRACT_VERSION, is_compatible

__all__ = [
    "CONTRACT_VERSION",
    "PROGRAMMING",
    "SIMULATED",
    "CommandName",
    "CommandResult",
    "CommandStatus",
    "ConfigApplied",
    "ConfigChange",
    "ConfigResult",
    "ConfigSnapshot",
    "ConfigState",
    "CoreCommand",
    "CoreStatus",
    "ProgrammingState",
    "DeviceAccess",
    "DeviceNeed",
    "DeliveryError",
    "Emitter",
    "FixQuality",
    "Freshness",
    "Header",
    "LinkEvents",
    "HealthCheck",
    "HealthReport",
    "HealthState",
    "IdempotencyLedger",
    "ListSink",
    "LocationSource",
    "LogEvent",
    "Message",
    "Metrics",
    "ModuleAnnounce",
    "ModuleManifest",
    "OMITTED",
    "Priority",
    "PttCommanded",
    "PttEvidence",
    "PttFeedback",
    "PttReason",
    "REDACTED",
    "ResourceBudget",
    "SafeState",
    "Sequencer",
    "Severity",
    "SiteLocation",
    "Source",
    "SpeakDecision",
    "SpeakRequest",
    "SpeakStatus",
    "StdoutSink",
    "StructuredError",
    "TransmissionEnded",
    "TransmissionStarted",
    "TxEndReason",
    "UnixDatagramSink",
    "core_event_adapter",
    "find_device_conflicts",
    "find_sensitive_keys",
    "is_compatible",
    "new_id",
    "redact",
    "screen_speak_request",
    "utc_now",
]
