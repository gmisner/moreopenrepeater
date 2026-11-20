"""Ordinary log records become structured events (#221, #229)."""
import json
import logging

import pytest

from contracts import LogEvent, Severity
from observability.events import (
    ProcessIdentity,
    correlation,
    log_event,
    record_to_event,
    snapshot,
)


@pytest.fixture
def identity():
    return ProcessIdentity("core", "0.1.0")


def make_record(message="hello", level=logging.INFO, name="moreopenrepeater.audio", args=(), exc_info=None, extra=None):
    record = logging.LogRecord(name, level, __file__, 1, message, args, exc_info)
    for key, value in (extra or {}).items():
        setattr(record, key, value)
    return record


def convert(record, identity):
    return record_to_event(getattr(record, "snap", None) or snapshot(record), identity, identity.sequencer.next())


def test_an_old_style_line_becomes_a_legacy_event_with_its_text(identity):
    event = convert(make_record("audio engine started (in=hw:2,0)"), identity)
    assert isinstance(event, LogEvent)
    assert event.event == "legacy.audio" and event.message == "audio engine started (in=hw:2,0)"
    assert event.module == "core" and event.module_version == "0.1.0" and event.severity is Severity.INFO
    assert event.context == {"logger": "moreopenrepeater.audio"}


@pytest.mark.parametrize("name,expected", [
    ("moreopenrepeater", "legacy.root"), ("moreopenrepeater.core.control", "legacy.core_control"),
    ("moreopenrepeater.Remote-Core", "legacy.remote_core"), ("something.else", "legacy.something_else"),
    ("moreopenrepeater.9lives", "legacy.x9lives"),
])
def test_a_logger_name_becomes_a_valid_event_name(identity, name, expected):
    assert convert(make_record(name=name), identity).event == expected


@pytest.mark.parametrize("level,expected", [
    (5, Severity.DEBUG), (10, Severity.DEBUG), (20, Severity.INFO), (25, Severity.INFO),
    (30, Severity.WARNING), (40, Severity.ERROR), (50, Severity.CRITICAL), (70, Severity.CRITICAL),
])
def test_levels_between_the_standard_ones_land_on_the_one_below(identity, level, expected):
    assert convert(make_record(level=level), identity).severity is expected


def test_the_message_is_interpolated_masked_and_cut_to_the_contract(identity):
    record = make_record("calling %s with %s", args=("https://user:hunter2@example.org/x", "Bearer abc.def"))
    event = convert(record, identity)
    assert "hunter2" not in event.message and "abc.def" not in event.message
    long = convert(make_record("x" * 5000), identity)
    assert len(long.message) <= 200


def test_an_exception_is_recorded_by_type_only_never_its_text(identity):
    try:
        raise ValueError("the pin was PIN-4821")  # letters outside hex, so a random boot id cannot contain it by chance
    except ValueError:
        import sys

        event = convert(make_record("could not do it", level=logging.ERROR, exc_info=sys.exc_info()), identity)
    assert event.error_code == "legacy.exception" and event.context["exception"] == "ValueError"
    line = event.to_json_line()
    assert "PIN-4821" not in line and "the pin was" not in line


def test_sequence_numbers_rise_and_the_boot_id_stays(identity):
    first, second = convert(make_record(), identity), convert(make_record(), identity)
    assert second.seq == first.seq + 1 and second.boot_id == first.boot_id
    assert ProcessIdentity("core", "0.1.0").sequencer.boot_id != identity.sequencer.boot_id  # a restart


def test_a_record_that_cannot_be_converted_still_gives_an_event(identity):
    record = make_record()
    record.created = float("nan")  # a time no one can use
    event = convert(record, identity)
    assert event.event == "log.unconvertible" and event.severity is Severity.WARNING


# -- the deliberate way ----------------------------------------------------------------------------


@pytest.fixture
def seen():
    logger = logging.getLogger("moreopenrepeater.test_events")
    logger.setLevel(logging.DEBUG)
    records = []

    class Keep(logging.Handler):
        def emit(self, record):
            record.snap = snapshot(record)  # taken now, in the logging thread, as the real handlers do
            records.append(record)

    handler = Keep()
    logger.addHandler(handler)
    yield logger, records
    logger.removeHandler(handler)


def test_log_event_carries_a_name_context_and_an_error_code(identity, seen):
    logger, records = seen
    log_event(logger, Severity.WARNING, "audio.glitch", "sound card ran dry", error_code="audio.overrun", count=1, state="idle")
    event = convert(records[0], identity)
    assert event.event == "audio.glitch" and event.severity is Severity.WARNING
    assert event.message == "sound card ran dry" and event.error_code == "audio.overrun"
    assert event.context == {"count": 1, "state": "idle"}


def test_its_text_line_shows_the_name_and_only_redacted_context(seen):
    logger, records = seen
    log_event(logger, Severity.INFO, "link.connect", node="41520", password="hunter2")
    text = records[0].getMessage()
    assert "link.connect" in text and "node=41520" in text and "hunter2" not in text


def test_context_is_redacted_when_the_event_is_built(identity, seen):
    logger, records = seen
    log_event(logger, Severity.INFO, "net.checkin", callsign="W1AW", dtmf_digits="1234", pin="9999", lat=33.5469)
    context = convert(records[0], identity).context
    assert context["callsign"] == "W1AW" and context["dtmf_digits"] == "[omitted]"
    assert context["pin"] == "[redacted]" and context["lat"] == 33.5


def test_a_bad_event_name_is_replaced_not_raised(identity, seen):
    logger, records = seen
    log_event(logger, Severity.INFO, "Not A Name", detail="x")
    event = convert(records[0], identity)
    assert event.event == "log.bad_event_name" and event.context["name"] == "Not A Name"


def test_nothing_is_built_for_a_level_that_is_off(seen):
    logger, records = seen
    logger.setLevel(logging.ERROR)
    log_event(logger, Severity.DEBUG, "audio.block", size=1)
    assert records == []


def test_a_correlation_id_set_around_work_is_stamped_on_what_is_logged(identity, seen):
    logger, records = seen
    with correlation("tx-0123456789ab") as current:
        assert current == "tx-0123456789ab"
        log_event(logger, Severity.INFO, "tx.started")
        logger.info("a plain line inside")
    log_event(logger, Severity.INFO, "tx.after")
    ids = [convert(r, identity).correlation_id for r in records]
    assert ids == ["tx-0123456789ab", "tx-0123456789ab", None]


def test_an_explicit_correlation_id_wins(identity, seen):
    logger, records = seen
    with correlation("tx-0123456789ab"):
        log_event(logger, Severity.INFO, "tx.other", correlation_id="rx-aaaaaaaaaaaa")
    assert convert(records[0], identity).correlation_id == "rx-aaaaaaaaaaaa"


def test_the_json_line_is_one_line_a_collector_can_read_back(identity, seen):
    logger, records = seen
    log_event(logger, Severity.INFO, "audio.engine_started", rate=16000)
    line = convert(records[0], identity).to_json_line()
    assert "\n" not in line and LogEvent.model_validate(json.loads(line)).event == "audio.engine_started"
