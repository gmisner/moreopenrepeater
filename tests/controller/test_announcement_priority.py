"""Announcement priority in the controller's queue (issue: priority for `speak.request`).

Priority orders what is waiting. It never interrupts a user, the ID or a
transmission in progress. Simulation on a fake clock only.
"""
import pytest

from controller.config import RepeaterConfig
from controller.events import PlayAudio
from controller.state_machine import (
    ANNOUNCING,
    IDLE,
    MAX_QUEUED_ANNOUNCEMENTS,
    PRIORITY_EMERGENCY,
    PRIORITY_HIGH,
    PRIORITY_LOW,
    PRIORITY_NORMAL,
    RepeaterController,
)


def controller(**config):
    return RepeaterController(
        RepeaterConfig(callsign="W1AW", id_interval=100000.0, **config), now=0.0, clip_duration=lambda clip: 1.0
    )


def play_order(c, now=1.0):
    """Let the channel go idle and collect what plays, back to back."""
    played = []
    while c.queued_announcements:
        commands = c.tick(now)
        played += [cmd.clip for cmd in commands if isinstance(cmd, PlayAudio)]
        now += 1.5
        commands = c.tick(now)
        played += [cmd.clip for cmd in commands if isinstance(cmd, PlayAudio)]
        now += 1.5
    return played


def test_the_constants_match_the_contract_enum():
    from contracts import Priority

    assert (PRIORITY_LOW, PRIORITY_NORMAL, PRIORITY_HIGH, PRIORITY_EMERGENCY) == (
        Priority.LOW, Priority.NORMAL, Priority.HIGH, Priority.EMERGENCY
    )


def test_the_default_priority_keeps_the_old_first_come_first_served_order():
    c = controller()
    for clip in ("a", "b", "c"):
        assert c.queue_announcement(clip)
    assert c.queued_announcements == ["a", "b", "c"]


def test_a_higher_priority_goes_ahead_of_lower_ones_but_not_ahead_of_its_equals():
    c = controller()
    c.queue_announcement("normal-1")
    c.queue_announcement("low", priority=PRIORITY_LOW)
    c.queue_announcement("normal-2")
    c.queue_announcement("high", priority=PRIORITY_HIGH)
    c.queue_announcement("emergency", priority=PRIORITY_EMERGENCY)
    c.queue_announcement("high-2", priority=PRIORITY_HIGH)
    assert c.queued_announcements == ["emergency", "high", "high-2", "normal-1", "normal-2", "low"]


def test_they_play_in_priority_order_when_the_channel_goes_idle():
    c = controller()
    c.queue_announcement("normal")
    c.queue_announcement("emergency", priority=PRIORITY_EMERGENCY)
    c.queue_announcement("low", priority=PRIORITY_LOW)
    assert play_order(c) == ["emergency", "normal", "low"]


def test_an_emergency_does_not_interrupt_a_transmission_in_progress():
    c = controller()
    c.queue_announcement("normal")
    c.tick(1.0)  # starts playing "normal"
    assert c.state == ANNOUNCING
    c.queue_announcement("emergency", priority=PRIORITY_EMERGENCY)
    assert c.state == ANNOUNCING  # nothing was cut off
    assert c.queued_announcements == ["emergency"]  # it waits its turn, first in line


def test_an_emergency_does_not_key_while_a_user_is_talking():
    from controller.events import COSChanged

    c = controller()
    c.handle_event(COSChanged(active=True), 1.0)
    c.queue_announcement("emergency", priority=PRIORITY_EMERGENCY)
    assert c.state != IDLE
    assert not any(isinstance(cmd, PlayAudio) for cmd in c.tick(1.5))
    assert c.queued_announcements == ["emergency"]


def fill(c, priority=PRIORITY_NORMAL, n=MAX_QUEUED_ANNOUNCEMENTS, prefix="n"):
    for i in range(n):
        assert c.queue_announcement(f"{prefix}{i}", priority=priority)


def test_a_full_queue_drops_a_new_clip_that_is_not_higher_than_the_lowest_waiting():
    c = controller()
    fill(c)
    assert not c.queue_announcement("late")  # same priority
    assert not c.queue_announcement("lower", priority=PRIORITY_LOW)
    assert len(c.queued_announcements) == MAX_QUEUED_ANNOUNCEMENTS and "late" not in c.queued_announcements


def test_a_higher_priority_takes_the_place_of_the_oldest_lowest_one_when_full():
    c = controller()
    fill(c, PRIORITY_LOW, 3, "low")
    fill(c, PRIORITY_NORMAL, MAX_QUEUED_ANNOUNCEMENTS - 3, "norm")
    assert c.queue_announcement("alert", priority=PRIORITY_HIGH)
    queue = c.queued_announcements
    assert len(queue) == MAX_QUEUED_ANNOUNCEMENTS
    assert queue[0] == "alert"
    assert "low0" not in queue and "low1" in queue and "low2" in queue  # the oldest low one went


def test_an_emergency_is_never_dropped_to_make_room():
    c = controller()
    fill(c, PRIORITY_EMERGENCY, MAX_QUEUED_ANNOUNCEMENTS, "e")
    assert not c.queue_announcement("another", priority=PRIORITY_EMERGENCY)  # equal: dropped, not swapped
    assert c.queued_announcements == [f"e{i}" for i in range(MAX_QUEUED_ANNOUNCEMENTS)]


def test_an_emergency_gets_in_even_when_the_queue_is_full_of_anything_lower():
    for lower in (PRIORITY_LOW, PRIORITY_NORMAL, PRIORITY_HIGH):
        c = controller()
        fill(c, lower)
        assert c.queue_announcement("emergency", priority=PRIORITY_EMERGENCY)
        assert c.queued_announcements[0] == "emergency"


def test_the_linked_radio_flag_stays_with_its_clip():
    c = controller()
    c.queue_announcement("plain")
    c.queue_announcement("linked", on_linked_radio=True, priority=PRIORITY_HIGH)
    commands = c.tick(1.0)
    play = next(cmd for cmd in commands if isinstance(cmd, PlayAudio))
    assert (play.clip, play.on_linked_radio) == ("linked", True)


@pytest.mark.parametrize("priority", [PRIORITY_LOW, PRIORITY_NORMAL, PRIORITY_HIGH, PRIORITY_EMERGENCY])
def test_a_disabled_transmitter_plays_nothing_whatever_the_priority(priority):
    c = controller(transmitter_enabled=False)
    c.queue_announcement("x", priority=priority)
    assert not any(isinstance(cmd, PlayAudio) for cmd in c.tick(1.0))
