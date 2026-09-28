import dataclasses
from datetime import datetime

from controller.announcements import (
    Announcement,
    AnnouncementScheduler,
    announcement_from_dict,
    next_occurrence,
)

MONDAY_9AM = datetime(2026, 9, 28, 9, 0)  # 2026-09-28 is a Monday


def weekly(times, days=None, **kwargs):
    extra = {} if days is None else {"days": days}
    return Announcement(id="w", name="weekly", message="hi", kind="weekly", times=times, **extra, **kwargs)


def test_interval_runs_every_n_minutes_after_the_reference_time():
    a = Announcement(id="a", name="a", message="hi", every_minutes=30)

    assert next_occurrence(a, MONDAY_9AM) == datetime(2026, 9, 28, 9, 30)


def test_weekly_picks_the_next_time_later_today():
    assert next_occurrence(weekly(["08:00", "19:30"]), MONDAY_9AM) == datetime(2026, 9, 28, 19, 30)


def test_weekly_rolls_over_to_the_next_allowed_day():
    # Only Wednesdays (2); from Monday 9am the next one is Wednesday 8am.
    assert next_occurrence(weekly(["08:00"], days=[2]), MONDAY_9AM) == datetime(2026, 9, 30, 8, 0)


def test_weekly_same_day_next_week_when_todays_time_has_passed():
    assert next_occurrence(weekly(["08:00"], days=[0]), MONDAY_9AM) == datetime(2026, 10, 5, 8, 0)


def test_weekly_time_exactly_now_is_not_due_again():
    assert next_occurrence(weekly(["09:00"], days=[0]), MONDAY_9AM) == datetime(2026, 10, 5, 9, 0)


def test_disabled_or_empty_schedules_never_run():
    assert next_occurrence(weekly(["08:00"], enabled=False), MONDAY_9AM) is None
    assert next_occurrence(weekly([]), MONDAY_9AM) is None
    assert next_occurrence(weekly(["08:00"], days=[]), MONDAY_9AM) is None


def test_scheduler_fires_once_when_due_and_reschedules():
    a = Announcement(id="a", name="a", message="hi", every_minutes=10)
    scheduler = AnnouncementScheduler([a], MONDAY_9AM)

    assert scheduler.due(datetime(2026, 9, 28, 9, 9)) == []
    assert scheduler.due(datetime(2026, 9, 28, 9, 10)) == [a]
    assert scheduler.due(datetime(2026, 9, 28, 9, 10, 30)) == []
    assert scheduler.next_run("a") == datetime(2026, 9, 28, 9, 20)


def test_missed_runs_fire_once_not_once_per_miss():
    a = Announcement(id="a", name="a", message="hi", every_minutes=10)
    scheduler = AnnouncementScheduler([a], MONDAY_9AM)

    assert scheduler.due(datetime(2026, 9, 28, 12, 0)) == [a]
    assert scheduler.next_run("a") == datetime(2026, 9, 28, 12, 10)


def test_editing_one_announcement_keeps_the_others_timers():
    a = Announcement(id="a", name="a", message="hi", every_minutes=10)
    b = Announcement(id="b", name="b", message="yo", every_minutes=10)
    scheduler = AnnouncementScheduler([a, b], MONDAY_9AM)

    scheduler.upsert(dataclasses.replace(b, message="changed"), datetime(2026, 9, 28, 9, 5))

    assert scheduler.next_run("a") == datetime(2026, 9, 28, 9, 10)
    assert scheduler.next_run("b") == datetime(2026, 9, 28, 9, 15)


def test_remove_drops_the_announcement():
    a = Announcement(id="a", name="a", message="hi")
    scheduler = AnnouncementScheduler([a], MONDAY_9AM)

    scheduler.remove("a", MONDAY_9AM)

    assert scheduler.list() == []
    assert scheduler.next_run("a") is None


def test_from_dict_ignores_unknown_fields():
    a = announcement_from_dict({"id": "a", "name": "a", "message": "hi", "from_the_future": 1})

    assert a == Announcement(id="a", name="a", message="hi")
