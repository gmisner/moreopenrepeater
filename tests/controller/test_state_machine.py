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
    LOCKOUT,
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


def test_timeout_noticed_at_unkey_returns_to_idle():
    controller = RepeaterController(make_config(tot_duration=2.0), now=0.0)
    controller.handle_event(COSChanged(active=True), now=0.0)

    commands = controller.handle_event(COSChanged(active=False), now=2.05)  # before the next tick

    assert PlayAudio(clip="timeout_tone") in commands
    assert controller.state == IDLE
    controller.handle_event(COSChanged(active=True), now=3.0)
    assert controller.state == RECEIVING


def lockout_config(**overrides):
    return make_config(
        **{"tot_duration": 10.0, "lockout_timeouts": 3, "lockout_window": 60.0, "lockout_clear_after": 20.0, **overrides}
    )


def test_carrier_that_never_drops_locks_out_after_repeated_timeouts():
    controller = RepeaterController(lockout_config(), now=0.0)
    controller.handle_event(COSChanged(active=True), now=0.0)

    controller.tick(now=10.0)
    assert controller.state == TIMEOUT
    controller.tick(now=20.0)
    assert controller.state == TIMEOUT and not controller.locked_out
    commands = controller.tick(now=30.0)

    assert controller.state == LOCKOUT
    assert controller.locked_out and controller.lockouts == 1
    assert AssertPTT(active=False) in commands


def test_flapping_carrier_locks_out_and_is_not_repeated():
    controller = RepeaterController(lockout_config(tot_duration=5.0), now=0.0)
    t = 0.0
    for _ in range(3):
        controller.handle_event(COSChanged(active=True), now=t)
        controller.tick(now=t + 5.0)
        controller.handle_event(COSChanged(active=False), now=t + 6.0)
        t += 7.0
    assert controller.state == LOCKOUT

    commands = controller.handle_event(COSChanged(active=True), now=t)

    assert controller.state == LOCKOUT
    assert AssertPTT(active=True) not in commands


def test_timeouts_spread_beyond_the_window_dont_lock_out():
    controller = RepeaterController(lockout_config(tot_duration=5.0, lockout_window=30.0), now=0.0)
    for t in (0.0, 20.0, 40.0, 60.0):
        controller.handle_event(COSChanged(active=True), now=t)
        controller.tick(now=t + 5.0)
        controller.handle_event(COSChanged(active=False), now=t + 6.0)

    assert not controller.locked_out
    assert controller.state == IDLE


def test_lockout_clears_once_the_channel_is_quiet():
    controller = RepeaterController(lockout_config(), now=0.0)
    controller.handle_event(COSChanged(active=True), now=0.0)
    for t in (10.0, 20.0, 30.0):
        controller.tick(now=t)
    assert controller.state == LOCKOUT

    controller.handle_event(COSChanged(active=False), now=40.0)
    controller.handle_event(COSChanged(active=True), now=50.0)  # back before the quiet period ends
    controller.handle_event(COSChanged(active=False), now=55.0)
    controller.tick(now=70.0)
    assert controller.state == LOCKOUT

    controller.tick(now=75.0)
    assert controller.state == IDLE and not controller.locked_out
    controller.handle_event(COSChanged(active=True), now=76.0)
    assert controller.state == RECEIVING


def test_lockout_still_ids():
    controller = RepeaterController(lockout_config(id_interval=50.0), now=0.0)
    controller.handle_event(COSChanged(active=True), now=0.0)
    for t in (10.0, 20.0, 30.0):
        controller.tick(now=t)

    commands = controller.tick(now=50.0)
    assert controller.state == TRANSMITTING_ID
    assert PlayAudio(clip="id") in commands

    commands = controller.tick(now=50.5)
    assert controller.state == LOCKOUT
    assert AssertPTT(active=False) in commands


def test_announcements_wait_for_the_lockout_to_clear():
    controller = RepeaterController(lockout_config(), now=0.0)
    controller.handle_event(COSChanged(active=True), now=0.0)
    for t in (10.0, 20.0, 30.0):
        controller.tick(now=t)
    controller.queue_announcement("tts:hello")

    controller.tick(now=31.0)
    assert controller.state == LOCKOUT

    controller.handle_event(COSChanged(active=False), now=32.0)
    controller.tick(now=52.0)
    assert controller.state == ANNOUNCING


def test_clearing_by_hand_repeats_a_carrier_that_is_still_there():
    controller = RepeaterController(lockout_config(), now=0.0)
    controller.handle_event(COSChanged(active=True), now=0.0)
    for t in (10.0, 20.0, 30.0):
        controller.tick(now=t)

    commands = controller.clear_lockout(now=31.0)

    assert controller.state == RECEIVING
    assert AssertPTT(active=True) in commands
    assert controller.clear_lockout(now=32.0) == []


def test_turning_the_lockout_off_clears_it_and_stops_counting():
    controller = RepeaterController(lockout_config(), now=0.0)
    controller.handle_event(COSChanged(active=True), now=0.0)
    for t in (10.0, 20.0, 30.0):
        controller.tick(now=t)

    controller.update_config(lockout_config(lockout_timeouts=0), now=31.0)
    assert not controller.locked_out
    assert controller.state == RECEIVING

    for t in (41.0, 51.0, 61.0, 71.0):
        controller.tick(now=t)
    assert controller.state == TIMEOUT


def test_patch_ending_during_a_lockout_returns_to_it():
    controller = RepeaterController(lockout_config(), now=0.0)
    controller.handle_event(COSChanged(active=True), now=0.0)
    for t in (10.0, 20.0, 30.0):
        controller.tick(now=t)
    controller.start_patch(now=31.0)
    assert controller.state == "patch"

    commands = controller.end_patch(now=40.0)

    assert controller.state == LOCKOUT
    assert AssertPTT(active=False) in commands


def test_ctcss_gating_ignores_key_up_without_matching_tone():
    controller = RepeaterController(make_config(require_ctcss_hz=100.0), now=0.0)

    controller.handle_event(COSChanged(active=True), now=0.0)
    assert controller.state == IDLE  # no CTCSS reported yet -- ignored

    controller.handle_event(CTCSSChanged(tone_hz=100.0), now=0.1)
    controller.handle_event(COSChanged(active=True), now=0.2)
    assert controller.state == RECEIVING


def test_id_timer_preempts_from_idle_and_resumes_idle():
    controller = RepeaterController(make_config(id_interval=1.0, id_audio_duration=0.5, idle_id=True), now=0.0)

    commands = controller.tick(now=1.0)
    assert controller.state == TRANSMITTING_ID
    assert PlayAudio(clip="id") in commands

    commands = controller.tick(now=1.6)
    assert controller.state == IDLE
    assert AssertPTT(active=False) in commands


def test_no_id_while_the_repeater_is_idle():
    controller = RepeaterController(make_config(), now=0.0)
    for now in (100.0, 500.0, 5_000.0):
        assert controller.tick(now) == []
    assert controller.state == IDLE


def test_first_key_up_starts_the_id_clock_and_one_id_follows_the_activity():
    controller = RepeaterController(make_config(), now=0.0)
    controller.tick(now=400.0)
    controller.handle_event(COSChanged(active=True), now=450.0)
    controller.handle_event(COSChanged(active=False), now=452.0)
    for now in (452.2, 453.3, 549.9):
        controller.tick(now)
    assert controller.state == IDLE

    assert PlayAudio(clip="id") in controller.tick(now=550.0)
    controller.tick(now=550.6)
    assert controller.state == IDLE
    assert controller.tick(now=650.0) == []
    assert controller.tick(now=2_000.0) == []


def test_ids_keep_coming_while_the_repeater_stays_in_use():
    controller = RepeaterController(make_config(), now=0.0)
    controller.handle_event(COSChanged(active=True), now=0.0)
    controller.handle_event(COSChanged(active=False), now=2.0)
    controller.tick(now=2.2)
    controller.tick(now=100.0)
    assert controller.state == TRANSMITTING_ID

    controller.tick(now=100.6)
    controller.handle_event(COSChanged(active=True), now=120.0)
    controller.handle_event(COSChanged(active=False), now=122.0)
    controller.tick(now=122.2)
    controller.tick(now=219.9)
    assert controller.state == IDLE
    assert PlayAudio(clip="id") in controller.tick(now=220.0)


def test_an_announcement_counts_as_activity():
    controller = RepeaterController(make_config(), now=0.0, clip_duration=lambda clip: 2.0)
    controller.queue_announcement("tts:net tonight")
    controller.tick(now=50.0)
    controller.tick(now=52.0)
    assert controller.state == IDLE
    assert PlayAudio(clip="id") in controller.tick(now=150.0)


def test_idle_id_ids_on_a_fixed_schedule_without_activity():
    controller = RepeaterController(make_config(idle_id=True), now=0.0)
    controller.handle_event(COSChanged(active=True), now=50.0)
    controller.handle_event(COSChanged(active=False), now=51.0)
    controller.tick(now=51.2)
    controller.tick(now=52.3)
    assert PlayAudio(clip="id") in controller.tick(now=100.0)
    controller.tick(now=100.6)
    assert PlayAudio(clip="id") in controller.tick(now=200.0)


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


def test_the_courtesy_tone_says_who_unkeyed_last():
    controller = RepeaterController(make_config(), now=0.0)
    controller.handle_event(RemoteKeyed(node_id="1998", keyed=True), now=0.0)
    assert controller.handle_event(RemoteKeyed(node_id="1998", keyed=False), now=1.0) == [PlayAudio("courtesy_tone_link")]

    controller = RepeaterController(make_config(), now=0.0)
    controller.handle_event(RemoteKeyed(node_id="1998", keyed=True), now=0.0)
    controller.handle_event(COSChanged(active=True), now=1.0)
    controller.handle_event(RemoteKeyed(node_id="1998", keyed=False), now=2.0)
    assert controller.handle_event(COSChanged(active=False), now=3.0) == [PlayAudio("courtesy_tone")]


def test_doubling_waits_for_both_the_user_and_the_link_to_unkey():
    controller = RepeaterController(make_config(), now=0.0)
    controller.handle_event(RemoteKeyed(node_id="1998", keyed=True), now=0.0)
    controller.handle_event(COSChanged(active=True), now=1.0)
    controller.handle_event(COSChanged(active=False), now=2.0)
    assert controller.state == RECEIVING  # the link is still talking
    controller.handle_event(RemoteKeyed(node_id="1998", keyed=False), now=3.0)
    assert controller.state == COURTESY_TONE

    controller = RepeaterController(make_config(), now=0.0)
    controller.handle_event(COSChanged(active=True), now=0.0)
    controller.handle_event(RemoteKeyed(node_id="1998", keyed=True), now=1.0)
    controller.handle_event(RemoteKeyed(node_id="1998", keyed=False), now=2.0)
    assert controller.state == RECEIVING  # the user is still talking
    controller.handle_event(COSChanged(active=False), now=3.0)
    assert controller.state == COURTESY_TONE


def test_a_carrier_without_the_required_tone_doesnt_hold_the_link_up():
    controller = RepeaterController(make_config(require_ctcss_hz=100.0), now=0.0)
    controller.handle_event(COSChanged(active=True), now=0.0)  # no tone: ignored
    controller.handle_event(RemoteKeyed(node_id="1998", keyed=True), now=1.0)
    controller.handle_event(RemoteKeyed(node_id="1998", keyed=False), now=2.0)
    assert controller.state == COURTESY_TONE


def test_changing_id_interval_reschedules_the_next_id_immediately():
    controller = RepeaterController(make_config(id_interval=600.0, idle_id=True), now=0.0)

    controller.update_config(make_config(id_interval=10.0, idle_id=True), now=5.0)

    controller.tick(now=14.9)
    assert controller.state == IDLE
    controller.tick(now=15.0)
    assert controller.state == TRANSMITTING_ID


def test_the_long_id_takes_the_place_of_the_regular_one_once_its_interval_passes():
    durations = {"id": 1.0, "id_long": 4.0}
    controller = RepeaterController(
        make_config(id_interval=100.0, idle_id=True, long_id_mode="voice", long_id_interval=300.0),
        now=0.0,
        clip_duration=durations.get,
    )
    played = []
    for now in range(0, 1001):
        played += [c.clip for c in controller.tick(float(now)) if isinstance(c, PlayAudio)]
    assert played == ["id", "id", "id_long", "id", "id", "id_long", "id", "id", "id_long", "id"]


def test_the_long_id_lasts_as_long_as_its_clip():
    controller = RepeaterController(
        make_config(id_interval=10.0, idle_id=True, long_id_mode="voice", long_id_interval=300.0),
        now=-300.0,
        clip_duration={"id": 1.0, "id_long": 4.0}.get,
    )
    assert PlayAudio(clip="id_long") in controller.tick(now=0.0)
    controller.tick(now=3.9)
    assert controller.state == TRANSMITTING_ID
    controller.tick(now=4.0)
    assert controller.state == IDLE


def test_no_long_id_when_it_is_off():
    controller = RepeaterController(make_config(id_interval=10.0, idle_id=True, long_id_interval=300.0), now=-300.0)
    assert PlayAudio(clip="id") in controller.tick(now=0.0)


def test_changing_long_id_interval_reschedules_the_next_long_id():
    controller = RepeaterController(
        make_config(id_interval=10.0, idle_id=True, long_id_mode="voice", long_id_interval=300.0), now=-300.0
    )
    controller.update_config(
        make_config(id_interval=10.0, idle_id=True, long_id_mode="voice", long_id_interval=600.0), now=-5.0
    )
    assert PlayAudio(clip="id") in controller.tick(now=0.0)


def test_a_short_transmission_doesnt_make_an_id_owed():
    controller = RepeaterController(make_config(id_skip_short_seconds=2.0), now=0.0)
    controller.handle_event(COSChanged(active=True), now=10.0)
    controller.tick(now=11.0)
    controller.handle_event(COSChanged(active=False), now=11.5)
    for now in (11.7, 13.0, 200.0, 2_000.0):
        assert not any(isinstance(c, PlayAudio) and c.clip == "id" for c in controller.tick(now))


def test_a_long_enough_transmission_owes_an_id_from_when_it_started():
    controller = RepeaterController(make_config(id_skip_short_seconds=2.0), now=0.0)
    controller.handle_event(COSChanged(active=True), now=10.0)
    controller.tick(now=12.0)
    controller.handle_event(COSChanged(active=False), now=30.0)
    controller.tick(now=30.2)
    controller.tick(now=31.3)
    controller.tick(now=109.9)
    assert controller.state == IDLE
    assert PlayAudio(clip="id") in controller.tick(now=110.0)


def test_a_transmission_counts_when_it_ends_after_the_threshold_between_ticks():
    controller = RepeaterController(make_config(id_skip_short_seconds=2.0), now=0.0)
    controller.handle_event(COSChanged(active=True), now=10.0)
    controller.handle_event(COSChanged(active=False), now=13.0)
    controller.tick(now=13.2)
    controller.tick(now=14.3)
    assert PlayAudio(clip="id") in controller.tick(now=110.0)


def test_announcements_always_owe_an_id_even_when_short_transmissions_are_skipped():
    controller = RepeaterController(
        make_config(id_skip_short_seconds=5.0), now=0.0, clip_duration=lambda clip: 1.0
    )
    controller.queue_announcement("tts:hello")
    controller.tick(now=50.0)
    controller.tick(now=51.0)
    assert PlayAudio(clip="id") in controller.tick(now=150.0)


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
        make_config(id_interval=10.0, id_audio_duration=0.5, idle_id=True), now=0.0, clip_duration=lambda clip: 3.0
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


def test_kerchunk_filter_ignores_short_key_ups():
    controller = RepeaterController(make_config(kerchunk_delay=0.3), now=0.0)

    assert controller.handle_event(COSChanged(active=True), now=0.0) == []
    controller.tick(now=0.1)
    assert controller.state == IDLE
    assert controller.handle_event(COSChanged(active=False), now=0.2) == []
    controller.tick(now=1.0)

    assert controller.state == IDLE
    assert controller.kerchunks_filtered == 1


def test_kerchunk_filter_keys_up_once_the_carrier_lasts():
    controller = RepeaterController(make_config(kerchunk_delay=0.3), now=0.0)
    controller.handle_event(COSChanged(active=True), now=0.0)

    assert controller.tick(now=0.25) == []
    assert AssertPTT(active=True) in controller.tick(now=0.3)
    assert controller.state == RECEIVING
    # The timeout timer runs from key-up, not from first carrier.
    controller.tick(now=5.2)
    assert controller.state == RECEIVING


def test_kerchunk_filter_does_not_delay_rekey_during_hang_time():
    controller = RepeaterController(make_config(kerchunk_delay=0.3), now=0.0)
    controller.handle_event(COSChanged(active=True), now=0.0)
    controller.tick(now=0.3)
    controller.handle_event(COSChanged(active=False), now=1.0)
    controller.tick(now=1.2)
    assert controller.state == HANG_TIME

    controller.handle_event(COSChanged(active=True), now=1.5)
    assert controller.state == RECEIVING


def test_kerchunk_filter_does_not_delay_linked_nodes():
    controller = RepeaterController(make_config(kerchunk_delay=0.3), now=0.0)
    controller.handle_event(RemoteKeyed(node_id="2000", keyed=True), now=0.0)
    assert controller.state == RECEIVING


def test_announcement_waits_while_a_key_up_is_pending():
    controller = RepeaterController(make_config(kerchunk_delay=0.3), now=0.0)
    controller.queue_announcement("tts:net tonight")
    controller.handle_event(COSChanged(active=True), now=0.0)

    controller.tick(now=0.1)
    assert controller.state == IDLE
    controller.tick(now=0.3)
    assert controller.state == RECEIVING
