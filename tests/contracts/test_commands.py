"""The commands the API sends to the core (Stage 3 slice 7c-4a, #229)."""
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from contracts import CommandName, CommandResult, CommandStatus, CoreCommand, Header, Source, StructuredError, new_id


def header(request_id=None):
    return Header(
        schema_version=1, message_id=new_id(), emitted_at=datetime.now(timezone.utc), boot_id=new_id(), seq=1,
        source=Source(module="api", version="0.1.0"), request_id=request_id,
    )


@pytest.mark.parametrize(
    "command, params",
    [
        ("clear_lockout", {}),
        ("set_net_active", {"active": True}),
        ("simulate_cos", {"active": False}),
        ("simulate_ctcss", {"tone_hz": 100.0}),
        ("simulate_ctcss", {"tone_hz": None}),
        ("simulate_dtmf", {"digit": "5"}),
        ("simulate_dtmf", {"digit": "#"}),
        ("simulate_remote_keyed", {"node_id": "41520", "keyed": True}),
    ],
)
def test_a_well_formed_command_is_accepted(command, params):
    assert CoreCommand(header=header(), command=command, params=params).command.value == command


@pytest.mark.parametrize(
    "command, params",
    [
        ("clear_lockout", {"extra": 1}),
        ("set_net_active", {}),
        ("set_net_active", {"active": "yes"}),
        ("set_net_active", {"active": 1}),
        ("simulate_cos", {"active": True, "x": 1}),
        ("simulate_ctcss", {"tone_hz": "100"}),
        ("simulate_ctcss", {"tone_hz": -1}),
        ("simulate_ctcss", {"tone_hz": float("inf")}),
        ("simulate_dtmf", {"digit": "55"}),
        ("simulate_dtmf", {"digit": "x"}),
        ("simulate_remote_keyed", {"node_id": "../etc", "keyed": True}),
        ("simulate_remote_keyed", {"node_id": "41520"}),
    ],
)
def test_a_malformed_command_is_refused(command, params):
    with pytest.raises(ValidationError):
        CoreCommand(header=header(), command=command, params=params)


def test_an_unknown_command_is_refused():
    with pytest.raises(ValidationError):
        CoreCommand(header=header(), command="reboot", params={})


def test_a_result_needs_a_request_and_a_rejection_needs_an_error():
    with pytest.raises(ValidationError):
        CommandResult(header=header(), status=CommandStatus.APPLIED)
    with pytest.raises(ValidationError):
        CommandResult(header=header(new_id()), status=CommandStatus.REJECTED)
    ok = CommandResult(
        header=header(new_id()), status=CommandStatus.REJECTED, error=StructuredError(code="command.x", message="no"),
    )
    assert ok.status is CommandStatus.REJECTED
    assert {c.value for c in CommandName} >= {"clear_lockout", "set_net_active"}


@pytest.mark.parametrize(
    "command, params",
    [
        ("add_macro", {"macro": {"pattern": "*71", "description": "x"}}),
        ("delete_macro", {"pattern": "*71"}),
        ("save_announcement", {"announcement": {"id": "a1", "name": "n", "message": "hi"}}),
        ("delete_announcement", {"announcement_id": "a1b2-c3_d4"}),
    ],
)
def test_programming_commands_are_accepted(command, params):
    assert CoreCommand(header=header(), command=command, params=params)


@pytest.mark.parametrize(
    "command, params",
    [
        ("add_macro", {"macro": "not an object"}),
        ("add_macro", {"macro": {str(i): i for i in range(40)}}),
        ("delete_macro", {"pattern": "../x"}),
        ("delete_macro", {"pattern": ""}),
        ("save_announcement", {"announcement": ["a"]}),
        ("delete_announcement", {"announcement_id": "a/b"}),
        ("delete_announcement", {"announcement_id": "x" * 65}),
    ],
)
def test_malformed_programming_commands_are_refused(command, params):
    with pytest.raises(ValidationError):
        CoreCommand(header=header(), command=command, params=params)
