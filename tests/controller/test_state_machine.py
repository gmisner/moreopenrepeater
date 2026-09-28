from controller.events import (
    AssertPTT,
    COSChanged,
    CTCSSChanged,
    DTMFDigit,
    PlayAudio,
    RemoteKeyed,
    SendLinkCommand,
)
from controller.macros import Macro
from controller.state_machine import (
    ANNOUNCING,
    ANNOUNCEMENT_FALLBACK_DURATION,
    MAX_QUEUED_ANNOUNCEMENTS,
    COURTESY_TONE,
    HANG_TIME,
    IDLE,
    RECEIVING,
    TIMEOUT,
    TRANSMITTING_ID,
    RepeaterConfig,
    RepeaterController,
)


def make_config(**overrides):
    defaults = dict(
        courtesy_tone_duration=0.2,
        hang_time=1.0,
        tot_duration=5.0,
        id_interval=100.0,
        id_audio_duration=0.5,
    )
    defaults.update(overrides)
    return RepeaterConfig(**defaults)


def test_full_receive_cycle_returns_to_idle():
    controller = RepeaterController(make_config(), now=0.0)
    assert controller.state == IDLE

    commands = controller.handle_event(COSChanged(active=True), now=0.0)
    assert controller.state == RECEIVING
    assert AssertPTT(active=True) in commands

    commands = controller.handle_event(COSChanged(active=False), now=1.0)
    assert controller.state == COURTESY_TONE
    assert PlayAudio(clip="courtesy_tone") in commands

    controller.tick(now=1.2)
    assert controller.state == HANG_TIME

    commands = controller.tick(now=2.3)
    assert controller.state == IDLE
    assert AssertPTT(active=False) in commands


def test_rekey_during_hang_time_returns_to_receiving():
    controller = RepeaterController(make_config(), now=0.0)
    controller.handle_event(COSChanged(active=True), now=0.0)
    controller.handle_event(COSChanged(active=False), now=1.0)
    controller.tick(now=1.2)
    assert controller.state == HANG_TIME

    controller.handle_event(COSChanged(active=True), now=1.5)
    assert controller.state == RECEIVING


def test_timeout_timer_kills_transmit_and_requires_unkey_to_clear():
    controller = RepeaterController(make_config(tot_duration=2.0), now=0.0)
    controller.handle_event(COSChanged(active=True), now=0.0)

    commands = controller.tick(now=2.1)
    assert controller.state == TIMEOUT
    assert AssertPTT(active=False) in commands

    controller.handle_event(COSChanged(active=True), now=2.2)
    assert controller.state == TIMEOUT  # still keyed -- ignored while timed out

    controller.handle_event(COSChanged(active=False), now=3.0)
    assert controller.state == IDLE


def test_ctcss_gating_ignores_key_up_without_matching_tone():
    controller = RepeaterController(make_config(require_ctcss_hz=100.0), now=0.0)

    controller.handle_event(COSChanged(active=True), now=0.0)
    assert controller.state == IDLE  # no CTCSS reported yet -- ignored

    controller.handle_event(CTCSSChanged(tone_hz=100.0), now=0.1)
    controller.handle_event(COSChanged(active=True), now=0.2)
    assert controller.state == RECEIVING


def test_id_timer_preempts_from_idle_and_resumes_idle():
    controller = RepeaterController(make_config(id_interval=1.0, id_audio_duration=0.5), now=0.0)

    commands = controller.tick(now=1.0)
    assert controller.state == TRANSMITTING_ID
    assert PlayAudio(clip="id") in commands

    commands = controller.tick(now=1.6)
    assert controller.state == IDLE
    assert AssertPTT(active=False) in commands


def test_dtmf_macro_dispatches_command():
    macro = Macro(
        pattern="*81",
        description="disconnect all links",
        command="disconnect_all",
        node_id="*",
    )
    controller = RepeaterController(make_config(), macros=[macro], now=0.0)

    assert controller.handle_event(DTMFDigit(digit="*"), now=0.0) == []
    assert controller.handle_event(DTMFDigit(digit="8"), now=0.1) == []
    commands = controller.handle_event(DTMFDigit(digit="1"), now=0.2)

    assert commands == [SendLinkCommand(node_id="*", command="disconnect_all")]


def test_remote_keyed_triggers_receiving_without_ctcss_gate():
    controller = RepeaterController(make_config(require_ctcss_hz=100.0), now=0.0)

    controller.handle_event(RemoteKeyed(node_id="1998", keyed=True), now=0.0)
    assert controller.state == RECEIVING

    controller.handle_event(RemoteKeyed(node_id="1998", keyed=False), now=1.0)
    assert controller.state == COURTESY_TONE


def test_changing_id_interval_reschedules_the_next_id_immediately():
    controller = RepeaterController(make_config(id_interval=600.0), now=0.0)

    controller.update_config(make_config(id_interval=10.0), now=5.0)

    controller.tick(now=14.9)
    assert controller.state == IDLE
    controller.tick(now=15.0)
    assert controller.state == TRANSMITTING_ID


def test_enabling_required_ctcss_closes_carrier_access_immediately():
    controller = RepeaterController(make_config(), now=0.0)

    controller.update_config(make_config(require_ctcss_hz=100.0), now=1.0)

    controller.handle_event(COSChanged(active=True), now=1.1)
    assert controller.state == IDLE


def test_enabling_required_ctcss_honours_a_tone_already_present():
    controller = RepeaterController(make_config(), now=0.0)
    controller.handle_event(CTCSSChanged(tone_hz=100.0), now=0.5)

    controller.update_config(make_config(require_ctcss_hz=100.0), now=1.0)

    controller.handle_event(COSChanged(active=True), now=1.1)
    assert controller.state == RECEIVING


def test_id_state_uses_the_reported_clip_duration_when_known():
    controller = RepeaterController(
        make_config(id_interval=10.0, id_audio_duration=0.5), now=0.0, clip_duration=lambda clip: 3.0
    )
    controller.tick(now=10.0)
    controller.tick(now=12.9)
    assert controller.state == TRANSMITTING_ID
    controller.tick(now=13.0)
    assert controller.state == IDLE


def test_announcement_plays_when_idle_for_its_clip_duration():
    controller = RepeaterController(make_config(), now=0.0, clip_duration=lambda clip: 2.0)
    controller.queue_announcement("tts:net tonight")

    commands = controller.tick(now=0.1)
    assert controller.state == ANNOUNCING
    assert commands == [AssertPTT(active=True), PlayAudio(clip="tts:net tonight")]

    assert controller.tick(now=2.0) == []
    commands = controller.tick(now=2.1)
    assert controller.state == IDLE
    assert commands == [AssertPTT(active=False)]


def test_announcement_waits_for_the_channel_to_clear():
    controller = RepeaterController(make_config(), now=0.0)
    controller.handle_event(COSChanged(active=True), now=0.0)
    controller.queue_announcement("tts:hello")

    controller.tick(now=0.5)
    assert controller.state == RECEIVING
    controller.handle_event(COSChanged(active=False), now=1.0)
    controller.tick(now=1.3)
    assert controller.state == HANG_TIME  # not during hang time either

    controller.tick(now=2.3)
    assert controller.state == ANNOUNCING


def test_queued_announcements_play_back_to_back_without_dropping_ptt():
    controller = RepeaterController(make_config(), now=0.0, clip_duration=lambda clip: 1.0)
    controller.queue_announcement("tts:one")
    controller.queue_announcement("tts:two")

    controller.tick(now=0.0)
    commands = controller.tick(now=1.0)

    assert controller.state == ANNOUNCING
    assert commands == [PlayAudio(clip="tts:two")]


def test_announcement_uses_fallback_duration_when_clip_length_unknown():
    controller = RepeaterController(make_config(), now=0.0)
    controller.queue_announcement("tts:hello")

    controller.tick(now=0.0)
    controller.tick(now=ANNOUNCEMENT_FALLBACK_DURATION - 0.1)
    assert controller.state == ANNOUNCING
    controller.tick(now=ANNOUNCEMENT_FALLBACK_DURATION)
    assert controller.state == IDLE


def test_announcement_queue_is_bounded():
    controller = RepeaterController(make_config(), now=0.0)
    for i in range(MAX_QUEUED_ANNOUNCEMENTS):
        assert controller.queue_announcement(f"tts:{i}")

    assert not controller.queue_announcement("tts:overflow")
    assert len(controller.queued_announcements) == MAX_QUEUED_ANNOUNCEMENTS


def test_key_up_during_announcement_is_ignored_like_during_id():
    controller = RepeaterController(make_config(), now=0.0, clip_duration=lambda clip: 1.0)
    controller.queue_announcement("tts:hello")
    controller.tick(now=0.0)

    assert controller.handle_event(COSChanged(active=True), now=0.5) == []
    assert controller.state == ANNOUNCING
