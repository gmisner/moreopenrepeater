from datetime import timedelta

import pytest
from pydantic import ValidationError

from contracts import (
    IdempotencyLedger, Header, Priority, SpeakDecision, SpeakRequest, SpeakStatus, Source, StructuredError,
    new_id, screen_speak_request, utc_now,
)


def header(now=None, **overrides):
    values = dict(
        schema_version=1, message_id=new_id(), emitted_at=now or utc_now(), boot_id=new_id(), seq=0,
        source=Source(module="geo", version="0.1.0"),
    )
    return Header(**{**values, **overrides})


def request(now=None, lifetime=timedelta(minutes=5), **overrides):
    now = now or utc_now()
    values = dict(
        header=header(now), idempotency_key=new_id(), topic="weather.alert", deadline=now + lifetime,
        text="Severe thunderstorm warning.",
    )
    return SpeakRequest(**{**values, **overrides})


def test_priority_orders_emergency_above_routine():
    assert Priority.EMERGENCY > Priority.HIGH > Priority.NORMAL > Priority.LOW


def test_deadline_is_required_and_must_follow_emission():
    now = utc_now()
    with pytest.raises(ValidationError):
        request(now, lifetime=timedelta(seconds=-1))
    with pytest.raises(ValidationError):
        request(now, lifetime=timedelta(0))
    with pytest.raises(ValidationError):
        SpeakRequest(header=header(now), idempotency_key=new_id(), topic="weather.alert", text="x")


def test_deadline_cannot_be_more_than_an_hour_out():
    request(lifetime=timedelta(hours=1))
    with pytest.raises(ValidationError):
        request(lifetime=timedelta(hours=1, seconds=1))


def test_text_is_required_and_bounded():
    with pytest.raises(ValidationError):
        request(text="")
    with pytest.raises(ValidationError):
        request(text="x" * 501)


def test_header_version_must_match_the_message_version():
    with pytest.raises(ValidationError):
        SpeakRequest(
            header=header(schema_version=2), idempotency_key=new_id(), topic="weather.alert",
            deadline=utc_now() + timedelta(minutes=1), text="x",
        )


def test_expired_request_is_dropped_and_not_remembered():
    now = utc_now()
    req = request(now, lifetime=timedelta(seconds=10))
    ledger = IdempotencyLedger()
    status, error = screen_speak_request(req, now + timedelta(seconds=10), ledger)
    assert status is SpeakStatus.EXPIRED and error.code == "speak.expired"
    assert len(ledger) == 0


def test_same_key_is_accepted_once_then_duplicate():
    now = utc_now()
    ledger = IdempotencyLedger()
    first = request(now, idempotency_key="alert-1-key")
    retry = request(now, idempotency_key="alert-1-key")
    assert screen_speak_request(first, now, ledger)[0] is SpeakStatus.ACCEPTED
    assert screen_speak_request(retry, now, ledger)[0] is SpeakStatus.DUPLICATE


def test_a_key_is_forgotten_after_its_deadline():
    now = utc_now()
    ledger = IdempotencyLedger()
    ledger.record("k" * 8, now + timedelta(seconds=5), now)
    assert not ledger.record("k" * 8, now + timedelta(seconds=5), now + timedelta(seconds=1))
    assert ledger.record("k" * 8, now + timedelta(seconds=65), now + timedelta(seconds=6))


def test_ledger_memory_is_bounded_and_evicts_the_oldest_deadline():
    now = utc_now()
    ledger = IdempotencyLedger(max_entries=3)
    for i, minutes in enumerate((30, 10, 20)):
        assert ledger.record(f"key-{i:04d}", now + timedelta(minutes=minutes), now)
    assert ledger.record("key-0003", now + timedelta(minutes=40), now)
    assert len(ledger) == 3
    assert ledger.record("key-0001", now + timedelta(minutes=50), now)  # the 10 minute one was evicted


def decision(status, request_id="req" + "0" * 10, **overrides):
    return SpeakDecision(
        header=header(source=Source(module="core", version="0.1.0"), request_id=request_id), status=status, **overrides
    )


def test_decision_rules():
    decision(SpeakStatus.ACCEPTED, transmission_id=new_id())
    decision(SpeakStatus.DUPLICATE)
    decision(SpeakStatus.REJECTED, error=StructuredError(code="speak.busy", message="Channel in use."))
    with pytest.raises(ValidationError):
        decision(SpeakStatus.ACCEPTED)
    with pytest.raises(ValidationError):
        decision(SpeakStatus.REJECTED)
    with pytest.raises(ValidationError):
        decision(SpeakStatus.DUPLICATE, request_id=None)


def test_idempotency_key_may_be_built_from_an_upstream_alert_id():
    request(idempotency_key="urn:oid:2.49.0.1.840.0.abc123")
    for bad in ("short", "has space in it", "x" * 129):
        with pytest.raises(ValidationError):
            request(idempotency_key=bad)
