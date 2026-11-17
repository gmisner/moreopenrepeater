from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from contracts import (
    CONTRACT_VERSION, REDACTED, Freshness, Header, LogEvent, Sequencer, Severity, Source,
    StructuredError, is_compatible, new_id, utc_now,
)
from contracts.versioning import parse_version


def make_header(**overrides):
    values = dict(
        schema_version=1, message_id=new_id(), emitted_at=utc_now(), boot_id=new_id(), seq=0,
        source=Source(module="core", version="0.1.0"),
    )
    return Header(**{**values, **overrides})


def test_header_rejects_unknown_fields_and_naive_times():
    with pytest.raises(ValidationError):
        make_header(surprise=1)
    with pytest.raises(ValidationError):
        make_header(emitted_at=datetime(2026, 1, 1))


def test_header_normalises_time_to_utc():
    local = datetime(2026, 1, 1, 12, 0, tzinfo=timezone(timedelta(hours=-7)))
    assert make_header(emitted_at=local).emitted_at.utcoffset() == timedelta(0)


def test_header_correlation_and_reply_ids_are_optional_but_validated():
    assert make_header().correlation_id is None
    with pytest.raises(ValidationError):
        make_header(correlation_id="no spaces allowed")


def test_sequencer_increases_and_boot_id_is_per_instance():
    a, b = Sequencer(), Sequencer()
    assert [a.next() for _ in range(3)] == [0, 1, 2]
    assert a.boot_id != b.boot_id


def test_structured_error_redacts_details():
    err = StructuredError(code="tts.unavailable", message="TTS is down", details={"token": "abc", "attempt": 2})
    assert err.details == {"token": REDACTED, "attempt": 2}


def test_structured_error_code_must_be_dotted_lowercase():
    with pytest.raises(ValidationError):
        StructuredError(code="TTSDown", message="x")


def test_freshness_goes_stale_after_max_age():
    seen = datetime(2026, 1, 1, tzinfo=timezone.utc)
    fresh = Freshness(observed_at=seen, max_age_s=30)
    assert not fresh.is_stale(seen + timedelta(seconds=30))
    assert fresh.is_stale(seen + timedelta(seconds=31))


def make_event(**overrides):
    values = dict(
        time=utc_now(), boot_id=new_id(), seq=1, severity=Severity.INFO, event="tx.started",
        module="core", module_version="0.1.0",
    )
    return LogEvent(**{**values, **overrides})


def test_log_event_context_is_redacted_on_construction():
    event = make_event(context={"password": "p", "latitude": 33.448, "digit_count": 4})
    assert event.context == {"password": REDACTED, "latitude": 33.4, "digit_count": 4}


def test_log_event_json_line_is_one_line_and_has_no_secret():
    event = make_event(correlation_id=new_id(), context={"api_key": "SECRET"})
    line = event.to_json_line()
    assert "\n" not in line and "SECRET" not in line
    assert '"severity":20' in line and '"schema_version":1' in line


def test_log_event_names_must_be_stable_dotted_identifiers():
    for bad in ("started", "TX.Started", "tx started now"):
        with pytest.raises(ValidationError):
            make_event(event=bad)


def test_ptt_commanded_and_feedback_are_distinct_valid_events():
    cid = new_id()
    commanded = make_event(event="ptt.commanded", correlation_id=cid, context={"keyed": True})
    feedback = make_event(event="ptt.feedback", correlation_id=cid, context={"keyed": True, "source": "sensor"})
    assert commanded.event != feedback.event and commanded.correlation_id == feedback.correlation_id


def test_severity_matches_logging_levels():
    import logging
    assert [s.value for s in Severity] == [logging.DEBUG, logging.INFO, logging.WARNING, logging.ERROR, logging.CRITICAL]


def test_contract_compatibility_rules():
    assert CONTRACT_VERSION == (1, 1)
    assert is_compatible((1, 0))  # a module built for 1.0 still works with a 1.1 core
    assert not is_compatible((1, 2))
    assert is_compatible((1, 0), (1, 2))
    assert not is_compatible((1, 3), (1, 2))
    assert not is_compatible((2, 0), (1, 2))
    assert parse_version("1.2") == (1, 2)
    for bad in ("1", "1.x", "1.2.3", ""):
        with pytest.raises(ValueError):
            parse_version(bad)
