"""One-time codes checked by the core (Stage 3 slice 7c-6c, #229)."""
import json
import os
import stat

import pytest

from api.control_codes import ControlCodes, new_secret, totp
from controller.events import CodedCommand, RunAction
from core.build import build_core
from core.codes import CoreCodeChecker, paths

NOW = 1_700_000_000.0  # a fixed time; the step is NOW // 30


class Clock:
    def __init__(self, now=NOW):
        self.now = now

    def __call__(self):
        return self.now


def code_for(secret, clock, offset=0):
    return totp(secret, int(clock.now // 30) + offset)


def write_secrets(path, users):
    path.write_text(json.dumps({"users": users}))


@pytest.fixture
def setup(tmp_path):
    clock = Clock()
    secrets_path, replay_path = paths(tmp_path)
    secret = new_secret()
    write_secrets(secrets_path, {"alice": {"secret": secret, "since": 1}})
    return clock, secrets_path, replay_path, secret


def make(setup):
    clock, secrets_path, replay_path, secret = setup
    return CoreCodeChecker(secrets_path, replay_path, clock), clock, secret


def test_a_good_code_is_accepted_once(setup):
    checker, clock, secret = make(setup)
    code = code_for(secret, clock)
    assert checker.check(code) == "alice"
    assert checker.check(code) is None  # the same code again


def test_a_step_either_side_is_allowed_but_not_further(setup):
    checker, clock, secret = make(setup)
    assert checker.check(code_for(secret, clock, -1)) == "alice"
    assert checker.check(code_for(secret, clock, +1)) == "alice"
    assert checker.check(code_for(secret, clock, 3)) is None


def test_a_used_code_cannot_be_replayed_after_the_core_restarts(setup):
    checker, clock, secret = make(setup)
    code = code_for(secret, clock)
    assert checker.check(code) == "alice"
    reborn = CoreCodeChecker(setup[1], setup[2], clock)  # a new process, same files
    assert reborn.check(code) is None
    assert reborn.check(code_for(secret, clock, 1)) == "alice"  # a later step is fine


def test_the_replay_record_is_private_and_the_secrets_file_is_never_written(setup):
    checker, clock, secret = make(setup)
    before = setup[1].read_text(), setup[1].stat().st_mtime_ns
    checker.check(code_for(secret, clock))
    assert stat.S_IMODE(os.stat(setup[2]).st_mode) == 0o600
    assert (setup[1].read_text(), setup[1].stat().st_mtime_ns) == before  # the API's file, untouched


def test_the_secrets_files_own_last_step_counts_too(setup):
    clock, secrets_path, replay_path, secret = setup
    step = int(clock.now // 30)
    write_secrets(secrets_path, {"alice": {"secret": secret, "last_step": step}})  # the API recorded a confirm at this step
    checker = CoreCodeChecker(secrets_path, replay_path, clock)
    assert checker.check(totp(secret, step)) is None
    assert checker.check(totp(secret, step + 1)) == "alice"


def test_five_wrong_codes_lock_everything_out_for_ten_minutes(setup):
    checker, clock, secret = make(setup)
    for _ in range(5):
        assert checker.check("000000") is None
    assert checker.locked()
    assert checker.check(code_for(secret, clock)) is None  # even the right one
    clock.now += 601
    assert not checker.locked()
    assert checker.check(code_for(secret, clock)) == "alice"


def test_a_right_code_clears_the_failures(setup):
    checker, clock, secret = make(setup)
    for _ in range(4):
        checker.check("000000")
    assert checker.check(code_for(secret, clock)) == "alice"
    for _ in range(4):
        checker.check("000000")
    assert not checker.locked()  # the count started over


def test_it_fails_closed_without_a_secrets_file(tmp_path):
    secrets_path, replay_path = paths(tmp_path)
    checker = CoreCodeChecker(secrets_path, replay_path, Clock())
    assert checker.check("123456") is None


@pytest.mark.parametrize("content", ["not json", "[]", '{"users": 5}', '{"users": {"a": "x"}}', ""])
def test_it_fails_closed_on_an_unreadable_secrets_file(setup, content):
    clock, secrets_path, replay_path, secret = setup
    secrets_path.write_text(content)
    assert CoreCodeChecker(secrets_path, replay_path, clock).check(code_for(secret, clock)) is None


def test_an_enrollment_made_while_it_runs_is_picked_up_and_a_removal_too(setup):
    checker, clock, secret = make(setup)
    bob = new_secret()
    assert checker.check(code_for(bob, clock)) is None
    write_secrets(setup[1], {"alice": {"secret": secret}, "bob": {"secret": bob}})
    os.utime(setup[1], ns=(1, 1_999_999_999))  # the API wrote the file
    assert checker.check(code_for(bob, clock)) == "bob"
    write_secrets(setup[1], {"bob": {"secret": bob}})
    os.utime(setup[1], ns=(2, 2_999_999_999))
    assert checker.check(code_for(secret, clock)) is None  # alice is gone


def test_a_pending_enrollment_is_not_a_secret_yet(setup):
    clock, secrets_path, replay_path, _ = setup
    pending = new_secret()
    write_secrets(secrets_path, {"carol": {"pending": pending}})
    assert CoreCodeChecker(secrets_path, replay_path, clock).check(code_for(pending, clock)) is None


def test_it_agrees_with_the_api_s_own_checker(setup):
    """The same files, the same clock: the core accepts exactly what the API's class would."""
    clock, secrets_path, replay_path, secret = setup
    api = ControlCodes(None, clock=clock)
    api._users = {"alice": {"secret": secret}}
    core_side = CoreCodeChecker(secrets_path, replay_path, clock)
    for offset in (-2, -1, 0, 1, 2):
        assert (core_side.check(code_for(secret, clock, offset)) is not None) == (api.check(code_for(secret, clock, offset)) is not None)


def test_a_failure_to_record_is_logged_and_the_code_still_works(setup, monkeypatch, caplog):
    checker, clock, secret = make(setup)
    monkeypatch.setattr(checker._replay, "save", lambda data: (_ for _ in ()).throw(OSError("disk full")))
    assert checker.check(code_for(secret, clock)) == "alice"
    assert "could not record the accepted code" in caplog.text


# -- in the core ---------------------------------------------------------------------------------


@pytest.fixture
def core_with_codes(tmp_path):
    (tmp_path / "state.json").write_text(json.dumps({"config": {"callsign": "W1AW", "audio_enabled": False}}))
    secret = new_secret()
    write_secrets(tmp_path / "control-codes.json", {"alice": {"secret": secret}})
    core = build_core({"MOREOPENREPEATER_DATA_DIR": str(tmp_path), "MOREOPENREPEATER_LOG_PATH": str(tmp_path / "c.log")})
    return core, secret


def test_the_core_checks_codes_itself_with_no_api(core_with_codes, monkeypatch):
    core, secret = core_with_codes
    said = []
    monkeypatch.setattr(core.service, "speak", lambda clip, on_linked_radio=False: said.append(clip))
    disabled = []
    monkeypatch.setattr(core.service, "update_config", lambda **kw: disabled.append(kw))
    good = totp(secret, int(__import__("time").time() // 30))
    programming = core.service.programming
    programming.run_coded(CodedCommand(command=RunAction(action="tx_disable", pattern="*0"), pattern="*0", code="000000"))
    assert [c.removeprefix("tts:") for c in said] == ["Code rejected."] and disabled == []
    programming.run_coded(CodedCommand(command=RunAction(action="tx_disable", pattern="*0"), pattern="*0", code=good))
    assert disabled == [{"transmitter_enabled": False}]


def test_the_core_alone_refuses_every_code_when_there_are_no_secrets(tmp_path, monkeypatch):
    (tmp_path / "state.json").write_text(json.dumps({"config": {"callsign": "W1AW", "audio_enabled": False}}))
    core = build_core({"MOREOPENREPEATER_DATA_DIR": str(tmp_path), "MOREOPENREPEATER_LOG_PATH": str(tmp_path / "c.log")})
    assert core.service.programming.code_checker("123456") is None
    assert core.service.programming.codes_locked() is False


def test_the_core_names_its_lockout_without_a_code_or_a_secret(setup, structured_events):
    checker, clock, secret = make(setup)
    for _ in range(5):
        checker.check("424242")
    locked = structured_events.named("codes.locked_out")
    assert len(locked) == 1 and locked[0].error_code == "codes.locked_out"
    assert locked[0].context == {"failures": 5, "lockout_min": 10}
    assert "424242" not in locked[0].model_dump_json() and secret not in locked[0].model_dump_json()
