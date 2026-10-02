from controller.events import COSChanged, DTMFDigit, MailboxCommand
from controller.mailbox import MailboxDialer, parse_entry
from controller.state_machine import RepeaterConfig, RepeaterController

CODES = {"leave": "*7", "play": "*8", "delete": "*9"}


def dial(dialer, digits, now=0.0):
    results = [dialer.handle_digit(d, now + i * 0.1, CODES) for i, d in enumerate(digits)]
    return [r for r in results if r is not None]


def test_parse_entry():
    assert parse_entry("leave", "12") == MailboxCommand("leave", "12")
    assert parse_entry("play", "12*1234") == MailboxCommand("play", "12", "1234")
    assert parse_entry("leave", "") is None
    assert parse_entry("leave", "1234567") is None
    assert parse_entry("play", "12") is None  # needs a PIN
    assert parse_entry("delete", "12*") is None


def test_dialer_reads_each_code():
    dialer = MailboxDialer()
    assert dial(dialer, "*712#") == [MailboxCommand("leave", "12")]
    assert dial(dialer, "*812*1234#") == [MailboxCommand("play", "12", "1234")]
    assert dial(dialer, "*912*99#") == [MailboxCommand("delete", "12", "99")]


def test_dialer_skips_noise_before_the_code():
    assert dial(MailboxDialer(), "5*812*1234#") == [MailboxCommand("play", "12", "1234")]


def test_dialer_finishes_on_unkey():
    dialer = MailboxDialer()
    assert dial(dialer, "*912*99") == []
    assert dialer.carrier_dropped(CODES) == MailboxCommand("delete", "12", "99")


def test_dialer_ignores_a_bare_code():
    dialer = MailboxDialer()
    assert dial(dialer, "*7#") == []
    assert dialer.carrier_dropped(CODES) is None


def controller(**overrides):
    config = RepeaterConfig(courtesy_tone_duration=0.2, hang_time=1.0, id_interval=100.0, id_audio_duration=0.5, **overrides)
    return RepeaterController(config, now=0.0)


def test_controller_emits_mailbox_commands_only_when_enabled():
    on = controller(mailbox_enabled=True)
    assert MailboxCommand("leave", "12") in [cmd for d in "*712#" for cmd in on.handle_event(DTMFDigit(d), 1.0)]

    off = controller()
    assert not [cmd for d in "*712#" for cmd in off.handle_event(DTMFDigit(d), 1.0) if isinstance(cmd, MailboxCommand)]


def test_controller_finishes_the_entry_on_unkey():
    c = controller(mailbox_enabled=True, mailbox_play_code="*55")
    c.handle_event(COSChanged(True), 1.0)
    for d in "*553*4321":
        c.handle_event(DTMFDigit(d), 1.5)
    assert MailboxCommand("play", "3", "4321") in c.handle_event(COSChanged(False), 2.0)
