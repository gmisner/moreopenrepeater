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


# -- the AllStar link service's commands -------------------------------------------------------------------


@pytest.mark.parametrize("event", ["connected", "disconnected", "keyed", "unkeyed"])
def test_a_link_event_names_a_node_and_what_happened(event):
    command = CoreCommand(header=header(), command="link_event", params={"node_id": "41520", "event": event})
    assert command.command is CommandName.LINK_EVENT


@pytest.mark.parametrize(
    "params",
    [
        {"node_id": "41520", "event": "exploded"},  # not one of the four
        {"node_id": "41520"},  # what happened is required
        {"event": "keyed"},
        {"node_id": "../etc", "event": "keyed"},  # not a node name
        {"node_id": "41520", "event": "keyed", "extra": 1},
        {"node_id": 41520, "event": "keyed"},  # a number is not a node name
        {"node_id": "41520", "event": None},
    ],
)
def test_a_malformed_link_event_is_refused(params):
    with pytest.raises(ValidationError):
        CoreCommand(header=header(), command="link_event", params=params)


def test_a_link_sync_carries_the_whole_picture():
    assert CoreCommand(header=header(), command="link_sync", params={"nodes": {}}).params == {"nodes": {}}
    command = CoreCommand(header=header(), command="link_sync", params={"nodes": {"41520": True, "53127": False}})
    assert command.params["nodes"] == {"41520": True, "53127": False}


@pytest.mark.parametrize(
    "nodes",
    [
        {"41520": "yes"},  # keyed is a yes or no, not a word
        {"41520": 1},
        {"../etc": True},
        {"": False},
        {str(n): False for n in range(65)},  # more than a repeater can have linked
        ["41520"],
        None,
        "41520",
    ],
)
def test_a_malformed_link_sync_is_refused(nodes):
    with pytest.raises(ValidationError):
        CoreCommand(header=header(), command="link_sync", params={"nodes": nodes})


def test_sixty_four_nodes_is_the_most_a_sync_takes():
    CoreCommand(header=header(), command="link_sync", params={"nodes": {str(n): False for n in range(64)}})


# --- mixer_run: amixer for the API, which has neither the program nor the sound card ---------------------------------

def mixer(args):
    return CoreCommand(header=header(), command="mixer_run", params={"args": args})


@pytest.mark.parametrize("args", [
    ["-c", "2", "scontents"],
    ["-c", "0", "scontents"],
    ["-q", "-c", "2", "sset", "Speaker", "75%"],
    ["-q", "-c", "12", "sset", "Mic Capture Volume", "capture", "80%"],
    ["-q", "-c", "2", "sset", "Mic", "18dB"],
    ["-q", "-c", "2", "sset", "Mic", "playback", "-3.5dB"],
    ["-q", "-c", "2", "sset", "Auto Gain Control", "off"],
    ["-q", "-c", "2", "sset", "PCM,0 (left)", "on"],
])
def test_the_two_amixer_forms_the_dashboard_uses_are_accepted(args):
    assert mixer(args).params["args"] == args


@pytest.mark.parametrize("args", [
    [],
    "scontents",
    ["-c", "2"],  # no verb
    ["-c", "2", "scontents", "--help"],  # nothing may ride along
    ["-c", "2", "controls"],  # another verb
    ["-c", "two", "scontents"],  # a card is a number
    ["-c", "2000", "scontents"],
    ["-c", "-1", "scontents"],
    ["--version"],
    ["-q", "-c", "2", "sset", "-h", "75%"],  # a control cannot be an option
    ["-q", "-c", "2", "sset", "--help", "75%"],
    ["-q", "-c", "2", "sset", "Speaker"],  # nothing to set it to
    ["-q", "-c", "2", "sset", "Speaker", "75%", "a", "b", "c", "d"],  # more than four values
    ["-q", "-c", "2", "sset", "Speaker", "75%;reboot"],
    ["-q", "-c", "2", "sset", "Speaker", "$(id)"],
    ["-q", "-c", "2", "sset", "Speaker", "75 %"],
    ["-q", "-c", "2", "sset", "Speaker", "x" * 17],
    ["-q", "-c", "2", "sset", "", "75%"],
    ["-q", "-c", "2", "sset", "x" * 65, "75%"],
    ["-q", "-c", "2", "sset", "Spea\nker", "75%"],
    ["-q", "-c", "2", "sset", "Spea\x00ker", "75%"],
    ["-q", "-c", "2", "set", "Speaker", "75%"],
    ["-c", "2", "-q", "sset", "Speaker", "75%"],  # the order is fixed
    ["-q", "-c", "2", "sset", 5, "75%"],
    ["-c", "2", "scontents"] + ["x"] * 12,  # too many
])
def test_anything_else_is_refused_before_it_gets_near_a_program(args):
    with pytest.raises(ValidationError):
        mixer(args)


def test_a_mixer_command_takes_the_args_and_nothing_else():
    with pytest.raises(ValidationError):
        CoreCommand(header=header(), command="mixer_run", params={"args": ["-c", "2", "scontents"], "program": "sh"})
    with pytest.raises(ValidationError):
        CoreCommand(header=header(), command="mixer_run", params={})


def test_a_command_result_can_carry_output_and_most_do_not():
    plain = CommandResult(header=header(request_id=new_id()), status=CommandStatus.APPLIED)
    assert plain.output is None
    with_output = CommandResult(
        header=header(request_id=new_id()), status=CommandStatus.APPLIED, output={"returncode": 0, "stdout": "x", "stderr": ""}
    )
    assert CommandResult.model_validate_json(with_output.model_dump_json()).output["stdout"] == "x"
