import base64
from datetime import datetime

import numpy as np
from fastapi.testclient import TestClient

from api.app import create_app
from api.audit import AuditLog
from api.auth import AuthSettings
from api.control_codes import LOCKOUT_SECONDS, MAX_FAILURES, ControlCodes, totp
from api.persistence import StateStore
from api.service import RepeaterService
from api.users import UserStore
from controller.events import CodedCommand, RunAction
from controller.macros import CODE_TIMEOUT, DTMFCommandDecoder, Macro
from controller.state_machine import RepeaterConfig

RFC_SECRET = base64.b32encode(b"12345678901234567890").decode()


class Clock:
    def __init__(self, now=1_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


def code_at(secret, clock, offset_steps=0):
    return totp(secret, int(clock.now // 30) + offset_steps)


def enrolled(codes, clock, username="alice"):
    secret = codes.begin(username)
    assert codes.confirm(username, code_at(secret, clock))
    clock.now += 30
    return secret


def test_totp_matches_rfc_6238():
    # RFC 6238 appendix B, SHA-1: 94287082 at T=59 and 07081804 at T=1111111109 (8 digits; we use the last 6).
    assert totp(RFC_SECRET, 59 // 30) == "287082"
    assert totp(RFC_SECRET, 1111111109 // 30) == "081804"


def test_setup_needs_a_matching_code(tmp_path):
    clock = Clock()
    codes = ControlCodes(StateStore(tmp_path / "codes.json"), clock)
    secret = codes.begin("alice")
    assert codes.status("alice") == {"enrolled": False, "since": None, "pending": True}
    assert not codes.confirm("alice", "000000" if code_at(secret, clock) != "000000" else "111111")
    assert codes.confirm("alice", code_at(secret, clock))
    assert codes.status("alice")["enrolled"]

    reloaded = ControlCodes(StateStore(tmp_path / "codes.json"), clock)
    assert reloaded.enrolled() == [{"username": "alice", "since": clock.now}]
    assert (tmp_path / "codes.json").stat().st_mode & 0o077 == 0


def test_code_works_once_and_only_while_fresh():
    clock = Clock()
    codes = ControlCodes(None, clock)
    secret = enrolled(codes, clock)

    code = code_at(secret, clock)
    assert codes.check(code) == "alice"
    assert codes.check(code) is None  # replayed

    clock.now += 30
    assert codes.check(code_at(secret, clock, offset_steps=-1)) is None  # older than the last one used
    assert codes.check(code_at(secret, clock, offset_steps=1)) == "alice"  # a phone clock running ahead
    clock.now += 300
    assert codes.check(code_at(secret, clock, offset_steps=-5)) is None


def test_each_user_has_their_own_secret():
    clock = Clock()
    codes = ControlCodes(None, clock)
    alice = enrolled(codes, clock, "alice")
    bob = enrolled(codes, clock, "bob")
    assert codes.check(code_at(bob, clock)) == "bob"
    assert codes.check(code_at(alice, clock)) == "alice"
    codes.remove("bob")
    clock.now += 30
    assert codes.check(code_at(bob, clock)) is None


def test_wrong_codes_lock_everyone_out_for_a_while():
    clock = Clock()
    codes = ControlCodes(None, clock)
    secret = enrolled(codes, clock)
    wrong = "123456" if code_at(secret, clock) != "123456" else "654321"
    for _ in range(MAX_FAILURES):
        assert codes.check(wrong) is None
    assert codes.locked()
    assert codes.check(code_at(secret, clock)) is None

    clock.now += LOCKOUT_SECONDS
    assert not codes.locked()
    assert codes.check(code_at(secret, clock)) == "alice"


def test_decoder_waits_for_six_digits_after_a_coded_macro():
    decoder = DTMFCommandDecoder([Macro("*9", "gate", command="open", action="say", needs_code=True)])
    assert decoder.handle_digit("*", 0.0) is None
    assert decoder.handle_digit("9", 0.1) is None
    for i, digit in enumerate("12345"):
        assert decoder.handle_digit(digit, 1.0 + i) is None
    command = decoder.handle_digit("6", 6.0)
    assert command == CodedCommand(command=RunAction("say", "open", "*9"), pattern="*9", code="123456")


def test_decoder_gives_up_on_a_slow_or_garbled_code():
    decoder = DTMFCommandDecoder([Macro("*9", "gate", command="open", action="say", needs_code=True)])
    for digit in "*9123":
        decoder.handle_digit(digit, 0.0)
    decoder.tick(CODE_TIMEOUT + 1)
    assert [decoder.handle_digit(d, CODE_TIMEOUT + 2) for d in "456"] == [None, None, None]

    for digit in "*912*":
        decoder.handle_digit(digit, 100.0)
    assert [decoder.handle_digit(d, 100.0) for d in "3456"] == [None, None, None, None]


class FakeTTS:
    name = "fake"

    def synthesize(self, text, voice=""):
        return np.full(8000, 0.1, dtype=np.float32), 8000


def make_service(clock):
    service = RepeaterService(
        config=RepeaterConfig(callsign="W1AW", hang_time=1.0),
        macros=[Macro("*9", "gate", command="Gate open", action="say", needs_code=True)],
        clock=clock,
        wall_clock=lambda: datetime(2026, 9, 28, 21, 5),
    )
    codes = ControlCodes(None, clock)
    service.code_checker = codes.check
    service.codes_locked = codes.locked
    audits = []
    service.audit_hook = lambda actor, action, detail: audits.append((actor, action, detail))
    return service, codes, audits


def dial(service, digits):
    for digit in digits:
        service.simulate_dtmf(digit)


def test_service_runs_a_coded_macro_with_a_good_code():
    clock = Clock()
    service, codes, audits = make_service(clock)
    secret = enrolled(codes, clock)
    dial(service, "*9" + code_at(secret, clock))
    assert service.controller.queued_announcements == ["tts:Gate open"]
    assert audits == [("DTMF (alice)", "DTMF (alice) say", "Gate open")]


def test_service_refuses_a_wrong_or_missing_code():
    clock = Clock()
    service, codes, audits = make_service(clock)
    secret = enrolled(codes, clock)
    wrong = "123456" if code_at(secret, clock) != "123456" else "654321"
    dial(service, "*9" + wrong)
    assert service.controller.queued_announcements == ["tts:Code rejected."]
    assert audits == [("DTMF", "DTMF code rejected", "*9")]

    dial(service, "*9")
    assert service.controller.queued_announcements == ["tts:Code rejected."]


def test_service_says_when_codes_are_locked_out():
    clock = Clock()
    service, codes, _audits = make_service(clock)
    enrolled(codes, clock)
    for _ in range(MAX_FAILURES - 1):
        codes.check("000000")
    dial(service, "*9000000")
    assert service.controller.queued_announcements[-1] == "tts:Codes are locked out. Try again later."


def test_trusted_sources_skip_the_code():
    clock = Clock()
    service, _codes, _audits = make_service(clock)
    service.run_macro("*9", "schedule")
    assert service.controller.queued_announcements == ["tts:Gate open"]


def make_client(clock, users=None, auth=None):
    service = RepeaterService()
    audit = AuditLog()
    codes = ControlCodes(None, clock)
    app = create_app(
        service=service,
        start_background_tick=False,
        auth_settings=auth,
        users=users if users is not None else UserStore(),
        audit=audit,
        control_codes=codes,
    )
    return TestClient(app), codes, audit


def basic(username, password):
    return {"Authorization": "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()}


def test_setting_up_codes_from_the_dashboard(tmp_path, monkeypatch):
    monkeypatch.setenv("MOREOPENREPEATER_LOG_PATH", str(tmp_path / "t.log"))
    clock = Clock()
    users = UserStore(reserved_username="admin")
    users.add("olly", "password1", "operator")
    users.add("val", "password1", "viewer")
    client, codes, audit = make_client(clock, users, AuthSettings("admin", "hunter2"))
    olly = basic("olly", "password1")

    assert client.get("/api/me/control-code", headers=olly).json() == {"enrolled": False, "since": None, "pending": False}
    setup = client.post("/api/me/control-code", headers=olly).json()
    assert setup["uri"].startswith("otpauth://totp/")
    assert setup["qr_svg"].startswith("<svg")
    assert client.post("/api/me/control-code/confirm", json={"code": "12345"}, headers=olly).status_code == 422
    response = client.post("/api/me/control-code/confirm", json={"code": code_at(setup["secret"], clock)}, headers=olly)
    assert response.json()["enrolled"] is True
    assert "secret" not in client.get("/api/me/control-code", headers=olly).text

    assert client.post("/api/me/control-code", headers=basic("val", "password1")).status_code == 403
    assert client.get("/api/control-codes", headers=olly).status_code == 403

    admin = basic("admin", "hunter2")
    assert [u["username"] for u in client.get("/api/control-codes", headers=admin).json()] == ["olly"]
    client.delete("/api/users/olly", headers=admin)
    assert client.get("/api/control-codes", headers=admin).json() == []
    assert ("olly", "POST /api/me/control-code/confirm") in [(e.actor, e.action) for e in audit.recent()]


def test_macros_carry_the_needs_code_flag(tmp_path, monkeypatch):
    monkeypatch.setenv("MOREOPENREPEATER_LOG_PATH", str(tmp_path / "t.log"))
    client, _codes, _audit = make_client(Clock())
    client.post("/api/macros", json={"pattern": "*9", "description": "gate", "command": "open", "action": "say", "needs_code": True})
    assert client.get("/api/macros").json()[-1]["needs_code"] is True
