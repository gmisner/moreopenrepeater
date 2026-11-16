"""Events the core publishes about a transmission.

PTT *commanded* and PTT *feedback* are separate events on purpose. The core
commanding a GPIO high proves the core asked, not that the transmitter keyed or
that RF went out. Feedback says what was observed and how: only independent
evidence (current sense, an RF detector) shows RF. A GPIO read-back shows only
the pin.
"""
from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal, Optional, Union

from pydantic import Field, TypeAdapter

from .common import ID_PATTERN, Message


class PttReason(str, Enum):
    TRANSMISSION = "transmission"  # normal start or end of a transmission
    TIMEOUT = "timeout"  # time-out timer expired
    WATCHDOG = "watchdog"  # a watchdog forced it off
    SHUTDOWN = "shutdown"  # clean stop or startup safe state
    FAULT = "fault"  # forced off after an error


class PttCommanded(Message):
    """The core told the PTT output to change state."""

    kind: Literal["ptt.commanded"] = "ptt.commanded"
    keyed: bool
    reason: PttReason
    transmission_id: Optional[str] = Field(default=None, pattern=ID_PATTERN)


class PttEvidence(str, Enum):
    NONE = "none"  # nothing observed
    GPIO_READBACK = "gpio_readback"  # the pin reads back as set: the pin only
    AUDIO_LOOPBACK = "audio_loopback"  # our own audio heard on the input
    CURRENT_SENSE = "current_sense"  # transmitter current measured
    RF_DETECT = "rf_detect"  # RF power detected

    @property
    def independent(self) -> bool:
        return self in (PttEvidence.CURRENT_SENSE, PttEvidence.RF_DETECT)


class PttFeedback(Message):
    """What was observed after a PTT command. `header.request_id` is the
    `message_id` of the `PttCommanded` it follows."""

    kind: Literal["ptt.feedback"] = "ptt.feedback"
    #: `None` means "could not tell", which is different from "not keyed".
    observed_keyed: Optional[bool]
    evidence: PttEvidence
    #: Milliseconds from the command to the observation.
    latency_ms: Optional[float] = Field(default=None, ge=0)
    transmission_id: Optional[str] = Field(default=None, pattern=ID_PATTERN)

    @property
    def proves_rf(self) -> bool:
        """True only when independent evidence says the transmitter is keyed."""
        return self.observed_keyed is True and self.evidence.independent


class TxEndReason(str, Enum):
    COMPLETE = "complete"
    TIMEOUT = "timeout"
    PREEMPTED = "preempted"
    FAULT = "fault"


class TransmissionStarted(Message):
    kind: Literal["tx.started"] = "tx.started"
    transmission_id: str = Field(pattern=ID_PATTERN)
    #: What is being sent: `id`, `announcement`, `speak.request`, `link`.
    source: str = Field(min_length=1, max_length=40)


class TransmissionEnded(Message):
    kind: Literal["tx.ended"] = "tx.ended"
    transmission_id: str = Field(pattern=ID_PATTERN)
    reason: TxEndReason
    duration_s: float = Field(ge=0)


CoreEvent = Annotated[
    Union[PttCommanded, PttFeedback, TransmissionStarted, TransmissionEnded],
    Field(discriminator="kind"),
]
core_event_adapter: TypeAdapter[CoreEvent] = TypeAdapter(CoreEvent)
