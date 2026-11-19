"""Named events with one correlation id per activity cycle (#221, #229)."""
import logging
import threading

from api.persistence import StateStore
from api.service import RepeaterService
from controller.events import COSChanged
from controller.state_machine import IDLE, RepeaterConfig, RepeaterController
from observability import begin_cycle, current_cycle, end_cycle, log_event

logger = logging.getLogger("moreopenrepeater.test_named")


def make_config(**overrides):
    defaults = dict(courtesy_tone_duration=0.2, hang_time=1.0, tot_duration=5.0, id_interval=100.0, id_audio_duration=0.5)
    defaults.update(overrides)
    return RepeaterConfig(**defaults)


def run_one_cycle(controller, start=0.0):
    controller.handle_event(COSChanged(active=True), now=start)
    controller.handle_event(COSChanged(active=False), now=start + 1.0)
    controller.tick(now=start + 1.2)
    controller.tick(now=start + 2.3)
    assert controller.state == IDLE


def test_a_message_that_says_it_all_keeps_the_text_line_and_the_event_keeps_the_facts(caplog, structured_events):
    with caplog.at_level(logging.INFO, logger="moreopenrepeater.test_named"):
        log_event(logger, logging.INFO, "demo.said", "all in words", count=3, facts_in_text=False)
        log_event(logger, logging.INFO, "demo.counted", "with facts", count=3)
    assert [r.getMessage() for r in caplog.records] == ["all in words", "with facts count=3"]
    assert structured_events.named("demo.said")[0].context == {"count": 3}


def test_everything_logged_during_a_cycle_by_any_thread_carries_its_id(structured_events):
    cycle = begin_cycle()
    assert current_cycle() == cycle
    log_event(logger, logging.INFO, "demo.in_cycle")
    logger.info("a plain old line")
    worker = threading.Thread(target=lambda: log_event(logger, logging.INFO, "demo.from_audio_thread"))
    worker.start()
    worker.join()
    end_cycle()
    log_event(logger, logging.INFO, "demo.after")
    assert [e.correlation_id for e in structured_events] == [cycle, cycle, cycle, None]
    assert structured_events.names()[1] == "legacy.test_named"


def test_an_explicit_correlation_id_wins_and_a_new_cycle_replaces_a_stuck_one(structured_events):
    from contracts import new_id

    mine = new_id()
    first = begin_cycle()
    log_event(logger, logging.INFO, "demo.explicit", correlation_id=mine)
    second = begin_cycle()  # the first was never ended
    assert second != first and current_cycle() == second
    assert structured_events[0].correlation_id == mine


def test_the_controller_opens_and_closes_a_cycle_around_the_repeater_being_busy(structured_events):
    controller = RepeaterController(make_config(), now=0.0)
    run_one_cycle(controller)
    changes = structured_events.named("controller.state_changed")
    assert [(e.context["from_state"], e.context["to_state"]) for e in changes] == [
        ("idle", "receiving"), ("receiving", "courtesy_tone"), ("courtesy_tone", "hang_time"), ("hang_time", "idle"),
    ]
    assert len({e.correlation_id for e in changes}) == 1 and changes[0].correlation_id is not None  # the first and the last included
    assert current_cycle() is None  # idle again: nothing is attributed to the cycle that ended

    run_one_cycle(controller, start=10.0)
    ids = [e.correlation_id for e in structured_events.named("controller.state_changed")]
    assert len(set(ids[:4])) == 1 and len(set(ids[4:])) == 1 and ids[0] != ids[4]  # the next cycle is a different one
    assert changes[0].message == "state idle -> receiving"


def test_the_service_logs_ptt_commanded_inside_the_cycle_it_belongs_to(tmp_path, structured_events):
    service = RepeaterService(state_store=StateStore(tmp_path / "state.json"))
    service.simulate_cos(True)
    service.simulate_cos(False)
    commanded = structured_events.named("ptt.commanded")
    assert [e.context["active"] for e in commanded] == [True]  # only a change is a command worth recording
    state_change = structured_events.named("controller.state_changed")[0]
    assert commanded[0].correlation_id == state_change.correlation_id is not None
    assert commanded[0].context["state"] == "receiving"


def test_a_repeated_ptt_command_is_not_a_new_event(tmp_path, structured_events):
    from controller.events import AssertPTT

    service = RepeaterService(state_store=StateStore(tmp_path / "state.json"))
    service._apply_commands([AssertPTT(active=True), AssertPTT(active=True), AssertPTT(active=False), AssertPTT(active=False)])
    assert [e.context["active"] for e in structured_events.named("ptt.commanded")] == [True, False]


def test_the_release_commanded_as_the_repeater_goes_idle_carries_the_cycle_it_ends(tmp_path, structured_events):
    """Found on the Pi: the controller went idle (ending the cycle) and only then was the unkey commanded and
    applied, so the end of every transmission had no id."""
    from controller.events import AssertPTT

    controller = RepeaterController(make_config(), now=0.0)
    service = RepeaterService(state_store=StateStore(tmp_path / "state.json"))
    service._apply_commands(controller.handle_event(COSChanged(active=True), now=0.0))
    controller.handle_event(COSChanged(active=False), now=1.0)
    controller.tick(now=1.2)
    commands = controller.tick(now=2.3)  # idle: the cycle ends and the release is commanded in the same step
    assert controller.state == IDLE and AssertPTT(active=False) in commands
    service._apply_commands(commands)
    commanded = structured_events.named("ptt.commanded")
    assert [e.context["active"] for e in commanded] == [True, False]
    assert commanded[0].correlation_id == commanded[1].correlation_id is not None


def test_a_cycle_that_ended_still_claims_events_for_a_moment_and_then_lets_go(monkeypatch, structured_events):
    from observability import events as events_module

    now = {"t": 100.0}
    monkeypatch.setattr(events_module, "_monotonic", lambda: now["t"])
    cycle = begin_cycle()
    end_cycle(grace_seconds=2.0)
    assert current_cycle() is None  # not in progress
    log_event(logger, logging.INFO, "demo.just_after")
    now["t"] = 101.9
    log_event(logger, logging.INFO, "demo.still_inside")
    now["t"] = 102.1
    log_event(logger, logging.INFO, "demo.too_late")
    assert [e.correlation_id for e in structured_events] == [cycle, cycle, None]


def test_a_new_cycle_ends_the_lingering_of_the_old_one(structured_events):
    old = begin_cycle()
    end_cycle(grace_seconds=60.0)
    new = begin_cycle()
    log_event(logger, logging.INFO, "demo.now")
    end_cycle()  # a strict end: nothing lingers, and the old cycle's grace is not revived
    log_event(logger, logging.INFO, "demo.later")
    assert [e.correlation_id for e in structured_events] == [new, None] and new != old
