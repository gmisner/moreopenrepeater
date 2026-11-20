"""The station's time zone: validated at the boundary, applied to the process, and followed by what holds a local time."""
import os
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from controller import station_clock
from controller.announcements import Announcement, AnnouncementScheduler
from playout.tts import spoken_time

# 2026-03-08 is the day the US clocks go forward: 02:00 EST becomes 03:00 EDT at 07:00 UTC.
BEFORE_SPRING_FORWARD = datetime(2026, 3, 8, 6, 59, tzinfo=timezone.utc).timestamp()
AFTER_SPRING_FORWARD = datetime(2026, 3, 8, 7, 1, tzinfo=timezone.utc).timestamp()
NOON_UTC = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc).timestamp()


def said_at(instant: float, zone: str) -> str:
    """What the station says for `instant` with its zone set to `zone`, read the way the renderer does: local time."""
    station_clock.apply_timezone(zone)
    return spoken_time(datetime.fromtimestamp(instant))


# -- validation ------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["America/Phoenix", "Asia/Kolkata", "Europe/London", "UTC", "America/Argentina/Buenos_Aires"])
def test_a_real_zone_name_is_accepted_as_it_is(name):
    assert station_clock.check_timezone(name) == name


@pytest.mark.parametrize("empty", ["", "   ", None])
def test_empty_means_the_containers_own_zone(empty):
    assert station_clock.check_timezone(empty) == ""


def test_surrounding_spaces_are_dropped():
    assert station_clock.check_timezone("  America/Phoenix ") == "America/Phoenix"


@pytest.mark.parametrize(
    "name",
    ["Mars/Base", "America", "america/phoenix", "../../etc/passwd", "/etc/localtime", "America/Phoenix\n; rm -rf", "posixrules", 7, ["UTC"]],
)
def test_anything_else_is_refused_with_a_plain_message(name):
    with pytest.raises(ValueError) as refused:
        station_clock.check_timezone(name)
    assert refused.value.args[0]  # says something a person can read
    assert "Traceback" not in str(refused.value)


def test_the_message_for_a_wrong_name_says_what_a_right_one_looks_like():
    with pytest.raises(ValueError, match="America/Phoenix"):
        station_clock.check_timezone("Mars/Base")


def test_a_refused_name_changes_nothing():
    station_clock.apply_timezone("Asia/Kolkata")
    before = (os.environ["TZ"], station_clock.generation(), time.timezone)
    with pytest.raises(ValueError):
        station_clock.apply_timezone("Mars/Base")
    assert (os.environ["TZ"], station_clock.generation(), time.timezone) == before


def test_the_list_offered_to_a_person_has_no_legacy_aliases():
    offered = station_clock.offered_zone_names()
    assert {"America/Phoenix", "Asia/Kolkata", "UTC"} <= set(offered)
    assert not [name for name in offered if name.startswith(("US/", "Etc/", "posix/", "right/"))]
    assert offered == sorted(offered)


# -- applying it -----------------------------------------------------------------------------------------------------


def test_the_spoken_time_for_one_instant_follows_the_zone():
    assert said_at(NOON_UTC, "America/Phoenix") == "5 o'clock A M"  # UTC-7, no daylight saving
    assert said_at(NOON_UTC, "Europe/London") == "12 o'clock P M"  # UTC+0 in November
    assert said_at(NOON_UTC, "Pacific/Auckland") == "1 o'clock A M"  # UTC+13 in November, the next day


def test_a_half_hour_zone_is_said_to_the_minute():
    assert said_at(NOON_UTC, "Asia/Kolkata") == "5 30 P M"  # UTC+5:30
    assert said_at(NOON_UTC + 5 * 60, "Asia/Kathmandu") == "5 50 P M"  # UTC+5:45


def test_the_clock_goes_forward_across_a_daylight_saving_boundary():
    assert said_at(BEFORE_SPRING_FORWARD, "America/New_York") == "1 59 A M"
    assert said_at(AFTER_SPRING_FORWARD, "America/New_York") == "3 oh 1 A M"  # 02:xx never happens that night
    assert said_at(AFTER_SPRING_FORWARD, "America/Phoenix") == "12 oh 1 A M"  # Phoenix keeps MST all year


def test_every_local_time_call_follows_not_only_the_one_that_speaks():
    station_clock.apply_timezone("Asia/Kolkata")
    assert datetime.fromtimestamp(NOON_UTC).hour == 17
    assert time.localtime(NOON_UTC).tm_hour == 17
    assert time.strftime("%H:%M", time.localtime(NOON_UTC)) == "17:30"
    assert datetime.fromtimestamp(NOON_UTC).astimezone().utcoffset() == timedelta(hours=5, minutes=30)
    # and datetime.now() is the same clock
    assert abs(datetime.now() - datetime.now(ZoneInfo("Asia/Kolkata")).replace(tzinfo=None)) < timedelta(seconds=5)


def test_the_process_is_told_to_reread_its_zone(monkeypatch):
    """glibc's localtime_r does not reread TZ by itself, so without tzset() a Linux container would keep the old zone
    (macOS rereads it anyway, which is why this is checked by the call rather than by the hour)."""
    calls = []
    monkeypatch.setattr(time, "tzset", lambda: calls.append(os.environ.get("TZ")))
    station_clock.apply_timezone("Asia/Kolkata")
    station_clock.apply_timezone("Asia/Kolkata")  # no change: nothing to reread
    station_clock.apply_timezone("")
    assert calls == ["Asia/Kolkata", os.environ.get("TZ")]
    assert calls[0] == "Asia/Kolkata"


def test_it_says_whether_the_zone_in_force_changed():
    assert station_clock.apply_timezone("America/Phoenix") is True
    assert station_clock.apply_timezone("America/Phoenix") is False
    assert station_clock.apply_timezone("Asia/Kolkata") is True


def test_empty_goes_back_to_the_container_s_own_zone(monkeypatch):
    monkeypatch.setattr(station_clock, "_BASE_TZ", "Asia/Tokyo")  # what the container started with (compose TZ)
    station_clock.apply_timezone("America/Phoenix")
    assert datetime.fromtimestamp(NOON_UTC).hour == 5
    assert station_clock.apply_timezone("") is True
    assert os.environ["TZ"] == "Asia/Tokyo"
    assert datetime.fromtimestamp(NOON_UTC).hour == 21


def test_empty_with_no_container_zone_goes_back_to_the_system_default(monkeypatch):
    monkeypatch.setattr(station_clock, "_BASE_TZ", None)
    station_clock.apply_timezone("America/Phoenix")
    station_clock.apply_timezone("")
    assert "TZ" not in os.environ


def test_setting_the_zone_it_is_already_on_is_not_a_change():
    before = station_clock.generation()
    assert station_clock.apply_timezone("") is False
    assert station_clock.generation() == before


# -- what holds a local time re-times itself ----------------------------------------------------------------------


def test_generation_and_shift_say_how_far_the_clock_moved():
    station_clock.apply_timezone("UTC")
    seen = station_clock.generation()
    station_clock.apply_timezone("Asia/Kolkata")
    assert station_clock.generation() == seen + 1
    assert station_clock.shift_since(seen) == timedelta(hours=5, minutes=30)
    station_clock.apply_timezone("America/Phoenix")
    assert station_clock.shift_since(seen) == timedelta(hours=-7)  # the two moves added up


def test_a_weekly_announcement_is_aimed_at_the_new_zone_s_clock():
    station_clock.apply_timezone("UTC")
    weekly = Announcement(id="a", name="Net reminder", kind="weekly", times=["19:30"])
    now = datetime(2026, 11, 20, 14, 0)  # 14:00 UTC, a Friday
    scheduler = AnnouncementScheduler([weekly], now)
    assert scheduler.next_run("a") == datetime(2026, 11, 20, 19, 30)
    station_clock.apply_timezone("Pacific/Auckland")  # UTC+13: it is already 03:00 Saturday there
    assert scheduler.due(datetime(2026, 11, 21, 3, 0)) == []  # 19:30 Friday is long past by that clock: not "due"
    assert scheduler.next_run("a") == datetime(2026, 11, 21, 19, 30)  # 19:30 Saturday, Auckland


def test_an_interval_announcement_counts_from_the_change():
    station_clock.apply_timezone("UTC")
    every_hour = Announcement(id="a", name="Hourly", kind="interval", every_minutes=60)
    scheduler = AnnouncementScheduler([every_hour], datetime(2026, 11, 20, 14, 0))
    station_clock.apply_timezone("America/Phoenix")
    assert scheduler.due(datetime(2026, 11, 20, 7, 0)) == []
    assert scheduler.next_run("a") == datetime(2026, 11, 20, 8, 0)
    assert [a.id for a in scheduler.due(datetime(2026, 11, 20, 8, 0))] == ["a"]


def test_editing_an_announcement_after_a_change_does_not_keep_the_other_one_s_old_time():
    station_clock.apply_timezone("UTC")
    a = Announcement(id="a", name="A", kind="weekly", times=["19:30"])
    scheduler = AnnouncementScheduler([a], datetime(2026, 11, 20, 14, 0))
    station_clock.apply_timezone("Pacific/Auckland")
    b = Announcement(id="b", name="B", kind="weekly", times=["08:00"])
    scheduler.upsert(b, datetime(2026, 11, 21, 3, 0))
    assert scheduler.next_run("a") == datetime(2026, 11, 21, 19, 30)


def test_without_a_change_an_announcement_keeps_its_time():
    scheduler = AnnouncementScheduler([Announcement(id="a", name="A", kind="weekly", times=["19:30"])], datetime(2026, 11, 20, 14, 0))
    assert scheduler.due(datetime(2026, 11, 20, 15, 0)) == []
    assert scheduler.next_run("a") == datetime(2026, 11, 20, 19, 30)
    assert [a.id for a in scheduler.due(datetime(2026, 11, 20, 19, 30))] == ["a"]


# -- where the default came from -----------------------------------------------------------------------------------------


def test_the_container_s_zone_is_the_default_and_is_named_as_the_source(monkeypatch):
    monkeypatch.setattr(station_clock, "_BASE_TZ", "America/Phoenix")
    assert station_clock.default_zone() == station_clock.DefaultZone("America/Phoenix", "container")


def test_a_container_zone_the_system_does_not_know_is_not_passed_off_as_one(monkeypatch):
    monkeypatch.setattr(station_clock, "_BASE_TZ", "Mars/Base")
    assert station_clock.default_zone() == station_clock.DefaultZone("", "unknown")


def test_with_no_container_zone_the_machine_s_own_setting_is_the_default(monkeypatch, tmp_path):
    monkeypatch.setattr(station_clock, "_BASE_TZ", None)
    zone = station_clock.default_zone()
    assert zone.source in ("system", "unknown")
    if zone.source == "system":
        assert zone.name in station_clock.zone_names()


# -- following a service ------------------------------------------------------------------------------------------------


class FakeService:
    def __init__(self):
        self.listeners = []

    def add_config_listener(self, listener):
        self.listeners.append(listener)

    def configure(self, config):
        for listener in self.listeners:
            listener(config)


class Config:
    def __init__(self, timezone):
        self.timezone = timezone


def test_follow_applies_the_saved_zone_now_and_on_every_change():
    service = FakeService()
    station_clock.follow(service.add_config_listener, Config("Asia/Kolkata"))
    assert station_clock.applied_timezone() == "Asia/Kolkata"
    service.configure(Config("America/Phoenix"))
    assert station_clock.applied_timezone() == "America/Phoenix"
    service.configure(Config(""))
    assert station_clock.applied_timezone() == ""


def test_follow_without_a_configuration_yet_waits_for_the_first_one():
    service = FakeService()
    station_clock.follow(service.add_config_listener, None)
    assert station_clock.applied_timezone() == ""
    service.configure(Config("Asia/Kolkata"))
    assert station_clock.applied_timezone() == "Asia/Kolkata"


def test_a_saved_zone_this_system_does_not_know_keeps_the_zone_in_force(caplog):
    service = FakeService()
    station_clock.follow(service.add_config_listener, Config("America/Phoenix"))
    service.configure(Config("Mars/Base"))
    assert station_clock.applied_timezone() == "America/Phoenix"
    assert "keeping the current one" in caplog.text
