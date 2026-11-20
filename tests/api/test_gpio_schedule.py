"""The GPIO output schedule: on at its start, off at its end, and unmoved by a change of the station's time zone."""
from datetime import datetime

from api.gpio import GpioScheduler
from controller import station_clock

BEACON = {"pin": 17, "days": [1], "time": "19:00", "minutes": 60, "enabled": True}


class FakeGpio:
    def __init__(self):
        self.calls = []

    def set_output(self, pin, on):
        self.calls.append((pin, on))


TUESDAY = datetime(2026, 9, 29)  # a Tuesday


def test_an_output_is_switched_on_at_the_start_and_off_at_the_end():
    gpio = FakeGpio()
    scheduler = GpioScheduler(gpio, lambda: [BEACON])
    scheduler.tick(TUESDAY.replace(hour=18, minute=59))
    assert gpio.calls == []
    scheduler.tick(TUESDAY.replace(hour=19, minute=1))
    assert gpio.calls == [(17, True)] and scheduler.until(17) == TUESDAY.replace(hour=20)
    scheduler.tick(TUESDAY.replace(hour=19, minute=30))
    assert gpio.calls == [(17, True)]  # once, not on every tick
    scheduler.tick(TUESDAY.replace(hour=20))
    assert gpio.calls == [(17, True), (17, False)] and scheduler.until(17) is None


def test_an_output_the_schedule_switched_on_is_still_switched_off_at_the_same_moment_after_a_zone_change():
    station_clock.apply_timezone("UTC")
    gpio = FakeGpio()
    scheduler = GpioScheduler(gpio, lambda: [BEACON])
    scheduler.tick(TUESDAY.replace(hour=19, minute=1))
    assert gpio.calls == [(17, True)] and scheduler.until(17) == TUESDAY.replace(hour=20)
    station_clock.apply_timezone("America/Phoenix")  # 19:01 UTC is 12:01 in Phoenix; the window ends in 59 minutes
    scheduler.tick(TUESDAY.replace(hour=12, minute=2))
    assert scheduler.until(17) == TUESDAY.replace(hour=13)
    assert gpio.calls == [(17, True)]  # not switched off early, and not switched on a second time
    scheduler.tick(TUESDAY.replace(hour=12, minute=30))
    assert scheduler.until(17) == TUESDAY.replace(hour=13)  # moved once for the change, not again on every tick
    assert gpio.calls == [(17, True)]
    scheduler.tick(TUESDAY.replace(hour=13))
    assert gpio.calls[-1] == (17, False)


def test_asking_for_the_status_after_a_zone_change_moves_the_end_too():
    station_clock.apply_timezone("UTC")
    scheduler = GpioScheduler(FakeGpio(), lambda: [BEACON])
    scheduler.tick(TUESDAY.replace(hour=19, minute=1))
    station_clock.apply_timezone("America/Phoenix")
    scheduler.status(TUESDAY.replace(hour=12, minute=2))
    assert scheduler.until(17) == TUESDAY.replace(hour=13)


def test_a_zone_change_with_nothing_switched_on_changes_nothing():
    station_clock.apply_timezone("UTC")
    gpio = FakeGpio()
    scheduler = GpioScheduler(gpio, lambda: [BEACON])
    station_clock.apply_timezone("America/Phoenix")
    scheduler.tick(TUESDAY.replace(hour=12))
    assert gpio.calls == [] and scheduler.until(17) is None


def test_a_scheduler_made_after_a_zone_change_does_not_replay_the_change():
    station_clock.apply_timezone("UTC")
    station_clock.apply_timezone("America/Phoenix")  # already in the past when the scheduler starts
    gpio = FakeGpio()
    scheduler = GpioScheduler(gpio, lambda: [BEACON])
    scheduler.tick(TUESDAY.replace(hour=19, minute=1))
    assert scheduler.until(17) == TUESDAY.replace(hour=20)
    scheduler.tick(TUESDAY.replace(hour=19, minute=30))
    assert scheduler.until(17) == TUESDAY.replace(hour=20) and gpio.calls == [(17, True)]
