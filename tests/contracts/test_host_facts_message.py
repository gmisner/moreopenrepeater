"""`core.host_facts`: what the core can see of its host (`GET /host-facts`)."""
import json
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from contracts import CoreHostFacts, Header, Source, new_id, schema


def header(version=1):
    return Header(
        schema_version=version, message_id=new_id(), emitted_at=datetime.now(timezone.utc), boot_id=new_id(), seq=1,
        source=Source(module="core", version="0.1.0"),
    )


def test_it_has_the_kind_the_core_and_the_dashboard_agree_on():
    message = CoreHostFacts(header=header(), kernel_log=["[1.0] usb 1-1: hi"])
    assert message.kind == "core.host_facts"
    assert json.loads(message.model_dump_json())["kind"] == "core.host_facts"


def test_it_carries_lines_or_the_reason_there_are_none():
    assert CoreHostFacts(header=header(), kernel_log=["a", "b"]).kernel_error is None
    assert CoreHostFacts(header=header(), kernel_error="permission denied").kernel_log is None


def test_an_empty_log_is_not_a_missing_one():
    assert CoreHostFacts(header=header(), kernel_log=[]).kernel_log == []


@pytest.mark.parametrize("error", [None, ""])
def test_a_missing_log_must_say_why(error):
    with pytest.raises(ValidationError, match="must say why"):
        CoreHostFacts(header=header(), kernel_log=None, kernel_error=error)


def test_it_is_bounded():
    with pytest.raises(ValidationError):
        CoreHostFacts(header=header(), kernel_log=["x"] * 1001)
    with pytest.raises(ValidationError):
        CoreHostFacts(header=header(), kernel_log=["x" * 1001])
    with pytest.raises(ValidationError):
        CoreHostFacts(header=header(), kernel_error="x" * 1001)


def test_it_refuses_fields_it_does_not_know_and_other_schema_versions():
    with pytest.raises(ValidationError):
        CoreHostFacts(header=header(), kernel_log=[], dmesg="no")
    with pytest.raises(ValidationError):
        CoreHostFacts(header=header(version=2), kernel_log=[])


def test_it_is_published_with_the_other_schemas():
    assert "core_host_facts.v1.json" in schema.MODELS and schema.MODELS["core_host_facts.v1.json"] is CoreHostFacts
    published = json.loads((schema.SCHEMA_DIR / "core_host_facts.v1.json").read_text())
    assert {"kernel_log", "kernel_error", "header", "kind"} <= set(published["properties"])
    assert published["required"] == ["header"]  # the lines and the reason are each optional; the model checks one is there
