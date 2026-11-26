"""The clock alert: when it is sent, when it is held back, when it clears, and the loop that runs the check."""
import asyncio
import logging

import pytest

from api import runtime as runtime_module
from api import time_sync as ts
from api.notify import Notifier
from api.runtime import RuntimeBuilder


def reading(offset=None, error=None, server="pool.ntp.org", at=0.0):
    if error:
        return ts.ClockReading(server, at, ts.SntpResult(False, error=error))
    return ts.ClockReading(server, at, ts.SntpResult(True, offset=offset, delay=0.02, stratum=2))


class Clock:
    def __init__(self, now=1_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


def alerts(clock=None):
    clock = clock or Clock()
    return ts.ClockAlerts(clock), clock


# -- when it is sent -----------------------------------------------------------------------------------------------------------


def test_a_clock_that_is_off_twice_running_raises_one_alert_and_the_first_reading_alone_does_not():
    machine, _ = alerts()
    assert machine.observe(reading(30.0)) == []  # one odd reading: a loaded Pi, a slow reply
    sent = machine.observe(reading(31.0))
    assert len(sent) == 1 and sent[0].key == "clock" and sent[0].severity == "warning"
    assert machine.observe(reading(32.0)) == []  # and not again on the next check


def test_a_good_reading_between_two_bad_ones_breaks_the_streak():
    machine, _ = alerts()
    machine.observe(reading(30.0))
    machine.observe(reading(0.1))
    assert machine.observe(reading(30.0)) == []  # one in a row again, not two


def test_the_limit_is_two_seconds_in_either_direction():
    for offset in (1.99, -1.99, 0.0):
        machine, _ = alerts()
        for _ in range(5):
            assert machine.observe(reading(offset)) == []
    for offset in (2.0, -2.0):
        machine, _ = alerts()
        machine.observe(reading(offset))
        assert len(machine.observe(reading(offset))) == 1


def test_the_alert_says_in_plain_words_what_a_wrong_clock_breaks_and_which_way_it_is_off():
    machine, _ = alerts()
    machine.observe(reading(120.0))
    behind = machine.observe(reading(120.0))[0]
    assert behind.title == "The Pi's clock is wrong" and "120 seconds behind" in behind.message
    for word in ("station ID", "spoken time", "scheduled announcements", "logs"):
        assert word in behind.message
    machine, _ = alerts()
    machine.observe(reading(-45.0))
    ahead = machine.observe(reading(-45.0))[0]
    assert "45 seconds ahead of" in ahead.message
    assert all("—" not in a.message and "—" not in a.title for a in (behind, ahead))


def test_what_to_do_depends_on_whether_the_time_container_is_running():
    machine, _ = alerts()
    machine.observe(reading(50.0), container_running=True)
    running = machine.observe(reading(50.0), container_running=True)[0].message
    machine, _ = alerts()
    machine.observe(reading(50.0))
    not_running = machine.observe(reading(50.0))[0].message
    assert "should correct it by itself" in running and "./setup.sh --time" not in running
    assert "./setup.sh --time" in not_running and "timedatectl set-ntp true" in not_running


def test_a_native_install_is_not_told_to_turn_on_a_time_container_it_does_not_have():
    machine, _ = alerts()
    machine.observe(reading(50.0), container_install=False)
    message = machine.observe(reading(50.0), container_install=False)[0].message
    assert "timedatectl set-ntp true" in message
    assert "time container" not in message and "setup.sh" not in message and "docker" not in message


# -- hysteresis and repeats ----------------------------------------------------------------------------------------------------


def test_it_clears_only_when_the_clock_is_within_a_second_so_one_that_hovers_at_the_limit_does_not_flap():
    machine, _ = alerts()
    machine.observe(reading(5.0))
    assert len(machine.observe(reading(5.0))) == 1
    for offset in (1.9, 1.5, 1.0, -1.2):  # better, but not back: still the same problem
        assert machine.observe(reading(offset)) == []
    cleared = machine.observe(reading(0.9))
    assert len(cleared) == 1 and cleared[0].severity == "info" and cleared[0].title == "Clock is correct again"
    assert machine.observe(reading(0.5)) == []  # said once


def test_a_clock_that_hovers_between_the_limits_without_ever_being_confirmed_never_alerts():
    machine, _ = alerts()
    for offset in (2.5, 1.5, 2.5, 1.5, 2.5, 1.5):
        assert machine.observe(reading(offset)) == []


def test_after_it_clears_the_next_problem_needs_two_readings_again():
    machine, _ = alerts()
    machine.observe(reading(5.0))
    machine.observe(reading(5.0))
    machine.observe(reading(0.0))
    assert machine.observe(reading(5.0)) == []
    assert len(machine.observe(reading(5.0))) == 1


def test_still_wrong_it_says_so_again_but_only_after_hours_not_on_every_check():
    machine, clock = alerts()
    machine.observe(reading(5.0))
    assert len(machine.observe(reading(5.0))) == 1
    for _ in range(20):
        clock.now += 600  # every ten minutes for three hours
        assert machine.observe(reading(5.0)) == []
    clock.now += ts.ALERT_REPEAT_SECONDS
    assert len(machine.observe(reading(5.0))) == 1
    clock.now += 600
    assert machine.observe(reading(5.0)) == []


def test_a_server_that_cannot_be_reached_says_nothing_about_the_clock():
    machine, _ = alerts()
    machine.observe(reading(5.0))
    for error in ("timeout", "resolve", "kiss", "unsynchronized", "bad_reply", "network"):
        assert machine.observe(reading(error=error)) == []
    assert len(machine.observe(reading(5.0))) == 1  # the unreachable readings neither counted nor reset the streak


def test_unreachable_does_not_clear_an_alert_either():
    machine, _ = alerts()
    machine.observe(reading(5.0))
    machine.observe(reading(5.0))
    assert machine.observe(reading(error="timeout")) == []
    assert len(machine.observe(reading(0.1))) == 1  # it clears when the clock is seen to be right, not when the check fails


def test_no_check_at_all_says_nothing():
    machine, _ = alerts()
    assert machine.observe(None) == []


def test_the_notifier_sends_it_once_and_holds_back_the_same_alert_inside_half_an_hour():
    """The alert has a stable key and title, so the notifier's own de-duplication applies on top of the streak and the repeat limit."""
    sent = []
    notifier = Notifier(senders={"ntfy": lambda settings, alert, title, timeout: sent.append(alert)})
    notifier.update(enabled=True, ntfy_url="https://ntfy.example/topic")
    machine, _ = alerts()
    machine.observe(reading(60.0))
    alert = machine.observe(reading(60.0))[0]

    async def scenario():
        await notifier.notify(alert)
        await notifier.notify(alert)

    asyncio.run(scenario())
    assert len(sent) == 1 and notifier.recent[0]["note"] == "not sent again so soon"


# -- the whole check ------------------------------------------------------------------------------------------------------------


def make_sync(tmp_path, results, **kwargs):
    setting = ts.NtpSetting(tmp_path / "cfg")
    answers = list(results)
    check = ts.ClockCheck(setting, lambda server: answers.pop(0) if len(answers) > 1 else answers[0], cache_seconds=0)
    return ts.TimeSync(setting, check, tmp_path / "status", **kwargs)


def test_poll_returns_the_alert_after_two_bad_checks_and_the_all_clear_after_a_good_one(tmp_path):
    bad = ts.SntpResult(True, offset=600.0, delay=0.01, stratum=2)
    good = ts.SntpResult(True, offset=0.01, delay=0.01, stratum=2)
    sync = make_sync(tmp_path, [bad, bad, bad, good, good])

    async def scenario():
        return [await sync.poll() for _ in range(5)]

    rounds = asyncio.run(scenario())
    assert [len(r) for r in rounds] == [0, 1, 0, 1, 0]
    assert rounds[1][0].title == "The Pi's clock is wrong" and rounds[3][0].title == "Clock is correct again"


def test_poll_knows_whether_the_time_container_is_running_for_the_wording(tmp_path):
    import json
    import time

    (tmp_path / "status").mkdir()
    (tmp_path / "status" / "status.json").write_text(json.dumps({"at": time.time(), "chronyd": True}))
    bad = ts.SntpResult(True, offset=600.0, delay=0.01, stratum=2)
    sync = make_sync(tmp_path, [bad])

    async def scenario():
        await sync.poll()
        return await sync.poll()

    assert "should correct it by itself" in asyncio.run(scenario())[0].message


def test_a_change_is_logged_once_as_a_named_event_with_numbers_only(tmp_path, structured_events):
    bad = ts.SntpResult(True, offset=600.0, delay=0.01, stratum=2)
    good = ts.SntpResult(True, offset=0.01, delay=0.01, stratum=2)
    gone = ts.SntpResult(False, error="timeout")
    sync = make_sync(tmp_path, [good, good, bad, bad, gone, gone, good])

    async def scenario():
        for _ in range(7):
            await sync.poll()

    asyncio.run(scenario())
    assert structured_events.names() == ["time.clock_off", "time.check_failed", "time.clock_ok"]  # the first good reading is silent
    off, failed, ok = (structured_events.named(n)[0] for n in ("time.clock_off", "time.check_failed", "time.clock_ok"))
    assert off.context == {"offset_ms": 600000, "limit_ms": 2000}
    assert failed.context == {"reason": "timeout"}
    assert ok.context == {"offset_ms": 10}
    assert "pool.ntp.org" not in repr([e.context for e in structured_events])


# -- the background loop ---------------------------------------------------------------------------------------------------------


class StubSync:
    """Stands in for TimeSync: what poll() returns, or raises, in turn, and then nothing."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    async def poll(self):
        self.calls += 1
        outcome = self.outcomes[self.calls - 1] if self.calls <= len(self.outcomes) else []
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async def status(self):
        return {}


@pytest.fixture
def quiet_runtime(monkeypatch):
    monkeypatch.setattr(runtime_module, "sd_notify", lambda message, env=None: True, raising=False)
    monkeypatch.setattr(runtime_module, "_kokoro_service_for", lambda renderer: None, raising=False)
    monkeypatch.setattr(runtime_module, "_piper_service_for", lambda renderer, voice="": None, raising=False)
    from api import core_runtime

    monkeypatch.setattr(core_runtime, "sd_notify", lambda message, env=None: True, raising=False)
    monkeypatch.setattr(core_runtime, "kokoro_service_for", lambda renderer: None)
    monkeypatch.setattr(core_runtime, "piper_service_for", lambda renderer, voice="": None)
    monkeypatch.setattr(runtime_module, "TIME_FIRST_CHECK_SECONDS", 0.0)
    monkeypatch.setattr(runtime_module, "TIME_CHECK_SECONDS", 0.05)


def run_loop(stub, seconds=0.4):
    from fastapi import FastAPI

    posted = []
    runtime = RuntimeBuilder(start_background_tick=True, time_sync=stub).build()
    runtime.notifier.post = posted.append
    runtime.attach_agent(FastAPI())

    async def scenario():
        async with runtime.lifespan(None):
            await asyncio.sleep(seconds)

    asyncio.run(scenario())
    return posted


def test_the_background_loop_checks_again_and_again_and_posts_what_it_is_given(quiet_runtime):
    alert = ts.Alert("clock", "The Pi's clock is wrong", "x", "warning")
    stub = StubSync([], [alert], [])
    posted = run_loop(stub)
    assert stub.calls >= 3 and posted == [alert]


def test_a_failing_check_is_logged_by_name_and_the_loop_carries_on(quiet_runtime, structured_events, caplog):
    alert = ts.Alert("clock", "The Pi's clock is wrong", "x", "warning")
    stub = StubSync(RuntimeError("boom"), [alert])
    with caplog.at_level(logging.ERROR):
        posted = run_loop(stub)
    assert posted == [alert]  # the second pass ran after the first one blew up
    failed = structured_events.named("time.loop_failed")
    assert len(failed) >= 1 and failed[0].error_code == "time.unexpected" and failed[0].context.get("exception") == "RuntimeError"


def test_the_loop_waits_before_its_first_check_so_a_container_that_is_about_to_fix_the_clock_gets_the_chance(monkeypatch, quiet_runtime):
    monkeypatch.setattr(runtime_module, "TIME_FIRST_CHECK_SECONDS", 60.0)
    stub = StubSync([])
    run_loop(stub, seconds=0.2)
    assert stub.calls == 0
    assert ts.FIRST_CHECK_SECONDS >= 60 and 600 <= ts.CHECK_SECONDS <= 900  # gently: every ten to fifteen minutes
