"""The core's control plane names what it does (#221, #229): commands, config changes, and DTMF."""
import logging

import pytest
from test_control_command import make_command, post_command
from test_control_command import run as run_commands
from test_control_config import SECRET, make_change, post_config
from test_control_config import run as run_config

from controller.events import DTMFDigit
from observability import log_event


def everything(events):
    return " ".join(e.model_dump_json() for e in events)


def test_a_command_that_is_applied_is_one_event_with_its_name_only(core, sock_dir, structured_events):
    run_commands(core, sock_dir, lambda path: post_command(path, make_command("set_net_active", active=True)))
    [applied] = structured_events.named("command.applied")
    assert applied.context == {"command": "set_net_active"} and applied.message == "core.command set_net_active applied"
    assert applied.severity == 20


def test_a_refused_command_says_why_with_a_code_you_can_filter_on(core, sock_dir, structured_events):
    run_commands(core, sock_dir, lambda path: post_command(path, make_command("simulate_cos", active=True)))
    [refused] = structured_events.named("command.refused")
    assert refused.error_code == "command.simulation_disabled" and refused.context == {"command": "simulate_cos"}
    assert refused.severity == 30
    assert structured_events.named("command.applied") == []


def test_a_command_that_blows_up_is_an_error_with_the_exception_type_and_never_its_text(core, sock_dir, structured_events, monkeypatch):
    def broken(active):
        raise RuntimeError("the secret detail " + SECRET)

    monkeypatch.setattr(core.service, "set_net_active", broken)
    run_commands(core, sock_dir, lambda path: post_command(path, make_command("set_net_active", active=True)))
    [failed] = structured_events.named("command.failed")
    assert failed.severity == 40 and failed.error_code == "command.apply_failed"
    assert failed.context == {"command": "set_net_active", "exception": "RuntimeError"}
    assert SECRET not in everything(structured_events)  # the exception's text is not recorded
    assert [e.event for e in structured_events.named("command.refused")] == ["command.refused"]  # and the client is told


def test_a_config_change_names_the_settings_and_never_the_values(core, sock_dir, structured_events):
    run_config(core, sock_dir, lambda path: post_config(path, make_change({"callsign": "W1AW", "tot_duration": 120})))
    [changed] = structured_events.named("config.changed")
    assert changed.context == {"settings": ["callsign", "tot_duration"]}
    assert "120" not in (changed.message or "").replace("2 settings", "")  # the value is not in the words either
    assert changed.message == "config.change applied (2 settings: callsign, tot_duration)"


def test_a_refused_config_change_carries_the_reason_as_its_code(core, sock_dir, structured_events):
    run_config(core, sock_dir, lambda path: post_config(path, make_change({"no_such_setting": SECRET})))
    [refused] = structured_events.named("config.refused")
    assert refused.error_code == "config.unknown_setting" and refused.severity == 30
    assert structured_events.named("config.changed") == []
    assert SECRET not in everything(structured_events)


def test_a_failed_config_change_is_an_error(core, sock_dir, structured_events, monkeypatch):
    def broken(**values):
        raise OSError(SECRET)

    monkeypatch.setattr(core.service, "update_config", broken)
    run_config(core, sock_dir, lambda path: post_config(path, make_change({"tot_duration": 120})))
    [failed] = structured_events.named("config.failed")
    assert failed.error_code == "config.apply_failed" and failed.context == {"exception": "OSError"}
    assert SECRET not in everything(structured_events)


@pytest.mark.parametrize("digit", ["5", "#", "*", "A"])
def test_a_dtmf_digit_is_counted_and_never_recorded(core, structured_events, digit, caplog):
    with caplog.at_level(logging.INFO, logger="moreopenrepeater"):
        core.service.handle_audio_events([DTMFDigit(digit=digit)])
    [received] = structured_events.named("dtmf.digit_received")
    assert received.context == {} and received.message == "DTMF digit received"
    assert f"'{digit}'" not in everything(structured_events)
    assert not [r for r in caplog.records if f"'{digit}'" in r.getMessage()]  # nor in the text log


def test_the_simulated_digit_is_not_logged_either(core, caplog):
    with caplog.at_level(logging.INFO, logger="moreopenrepeater"):
        core.service.simulate_dtmf("7")
    assert not [r for r in caplog.records if "7" in r.getMessage()]


def test_log_event_can_carry_a_traceback_to_the_text_log_and_the_type_to_the_event(structured_events, caplog):
    logger = logging.getLogger("moreopenrepeater.test_exc")
    with caplog.at_level(logging.INFO, logger="moreopenrepeater.test_exc"):
        try:
            raise ValueError("private text")
        except ValueError:
            log_event(logger, logging.ERROR, "demo.failed", "it failed", exc_info=True)
    assert caplog.records[0].exc_info is not None  # the traceback is in the text log, as with logger.exception
    [event] = structured_events.named("demo.failed")
    assert event.context == {"exception": "ValueError"} and "private text" not in event.model_dump_json()
