from controller.autopatch import AutopatchDialer, matches, number_allowed, valid_patterns
from controller.events import AssertPTT, COSChanged, DialPatch, DTMFDigit, HangupPatch, PlayAudio
from controller.state_machine import (
    COURTESY_TONE,
    IDLE,
    PATCH,
    RECEIVING,
    TRANSMITTING_ID,
    RepeaterConfig,
    RepeaterController,
)

ALLOWED = "911 NXXNXXXXXX"
BLOCKED = "900XXXXXXX NXX976XXXX"


def dial(dialer, digits, now=0.0, access="*6", hangup="#"):
    results = [dialer.handle_digit(d, now + i * 0.1, access, hangup) for i, d in enumerate(digits)]
    return [r for r in results if r is not None]


def test_pattern_classes():
    assert matches("8605551234", "NXXNXXXXXX")
    assert not matches("1605551234", "NXXNXXXXXX")  # area codes don't start with 1
    assert not matches("86055512345", "NXXNXXXXXX")
    assert matches("911", "911") and not matches("912", "911")
    assert matches("15", "Z5") and not matches("05", "Z5")


def test_number_rules_allow_local_and_emergency_but_not_toll_calls():
    assert number_allowed("911", ALLOWED, BLOCKED)
    assert number_allowed("8605551234", ALLOWED, BLOCKED)
    assert not number_allowed("18605551234", ALLOWED, BLOCKED)  # long distance
    assert not number_allowed("411", ALLOWED, BLOCKED)
    assert not number_allowed("9005551234", ALLOWED, BLOCKED)
    assert not number_allowed("8609761234", ALLOWED, BLOCKED)
    assert not number_allowed("", ALLOWED, BLOCKED)


def test_pattern_validation():
    assert valid_patterns("911, nxxnxxxxxx\nZXX")
    assert valid_patterns("")
    assert not valid_patterns("911 1-800")


def test_access_code_number_and_pound_dials():
    assert dial(AutopatchDialer(), "*68605551234#") == [DialPatch("8605551234")]


def test_unkeying_after_the_number_dials_it():
    dialer = AutopatchDialer()
    assert dial(dialer, "*6911") == []
    assert dialer.carrier_dropped("*6") == DialPatch("911")


def test_unkeying_after_just_the_access_code_does_nothing():
    dialer = AutopatchDialer()
    dial(dialer, "*6")
    assert dialer.carrier_dropped("*6") is None
    assert dial(dialer, "911#") == []  # the access code has to be sent again


def test_digits_before_the_access_code_are_ignored():
    assert dial(AutopatchDialer(), "12*6911#") == [DialPatch("911")]
    assert dial(AutopatchDialer(), "**6911#") == [DialPatch("911")]


def test_star_in_the_number_cancels():
    assert dial(AutopatchDialer(), "*691*1#") == []


def test_slow_digits_time_out():
    dialer = AutopatchDialer(interdigit_timeout=5.0)
    dial(dialer, "*6")
    assert dialer.handle_digit("9", 10.0, "*6", "#") is None
    assert dial(dialer, "11#", now=10.1) == []


def test_hangup_code_only_works_during_a_call():
    dialer = AutopatchDialer()
    assert dial(dialer, "#") == []
    dialer.call_active = True
    assert dial(dialer, "#") == [HangupPatch()]
    assert dial(dialer, "*6911#") == [HangupPatch()]  # no second call; the trailing # still hangs up


def test_multi_digit_hangup_code():
    dialer = AutopatchDialer()
    dialer.call_active = True
    assert dial(dialer, "7##", hangup="##") == [HangupPatch()]
    assert dial(dialer, "#5#", hangup="##") == []


def controller(**overrides):
    config = RepeaterConfig(
        courtesy_tone_duration=0.2, hang_time=1.0, id_interval=100.0, id_audio_duration=0.5,
        autopatch_enabled=True, **overrides,
    )
    return RepeaterController(config, now=0.0)


def test_controller_emits_dial_only_when_autopatch_is_enabled():
    c = controller()
    commands = [cmd for d in "*6911#" for cmd in c.handle_event(DTMFDigit(d), 1.0)]
    assert DialPatch("911") in commands

    off = RepeaterController(RepeaterConfig(), now=0.0)
    assert not [cmd for d in "*6911#" for cmd in off.handle_event(DTMFDigit(d), 1.0) if isinstance(cmd, DialPatch)]


def test_controller_dials_on_unkey():
    c = controller()
    c.handle_event(COSChanged(True), 1.0)
    for d in "*6911":
        c.handle_event(DTMFDigit(d), 1.5)
    commands = c.handle_event(COSChanged(False), 2.0)
    assert PlayAudio("courtesy_tone") in commands
    assert DialPatch("911") in commands


def test_patch_holds_the_transmitter_through_unkeys():
    c = controller()
    c.handle_event(COSChanged(True), 1.0)
    assert c.start_patch(1.5) == [AssertPTT(True)]
    assert c.state == PATCH
    assert c.handle_event(COSChanged(False), 2.0) == []
    c.tick(50.0)  # no courtesy tone, hang time or timeout during a call
    assert c.state == PATCH
    assert c.handle_event(COSChanged(True), 51.0) == []
    assert c.state == PATCH


def test_ending_the_patch_goes_through_courtesy_tone_to_idle():
    c = controller()
    c.start_patch(1.0)
    assert c.end_patch(5.0) == [PlayAudio("courtesy_tone")]
    assert c.state == COURTESY_TONE
    c.tick(5.3)
    c.tick(6.5)
    assert c.state == IDLE


def test_ending_the_patch_while_a_user_talks_goes_back_to_repeating():
    c = controller()
    c.start_patch(1.0)
    c.handle_event(COSChanged(True), 2.0)
    assert c.end_patch(3.0) == [AssertPTT(True)]
    assert c.state == RECEIVING


def test_station_id_during_a_call_returns_to_the_call():
    c = controller()
    c.start_patch(1.0)
    assert c.tick(100.0) == [AssertPTT(True), PlayAudio("id")]
    assert c.state == TRANSMITTING_ID
    assert c.tick(100.6) == []
    assert c.state == PATCH


def test_call_ending_during_the_id_drops_to_idle_after_it():
    c = controller()
    c.start_patch(1.0)
    c.tick(100.0)
    assert c.end_patch(100.2) == []
    assert c.tick(100.6) == [AssertPTT(False)]
    assert c.state == IDLE


def test_no_patch_while_the_transmitter_is_disabled():
    c = controller(transmitter_enabled=False)
    assert c.start_patch(1.0) == []
    assert c.state == IDLE


def test_hangup_code_reaches_the_controller_during_a_call():
    c = controller()
    c.set_patch_call_active(True)
    assert HangupPatch() in c.handle_event(DTMFDigit("#"), 1.0)
