from controller.events import LinkStateChanged, RemoteKeyed, SendLinkCommand
from controller.macros import Macro
from controller.state_machine import RECEIVING, RepeaterConfig

from api.service import RepeaterService


def make_service(macros=None, **config_overrides):
    clock = {"now": 0.0}
    config = RepeaterConfig(
        courtesy_tone_duration=0.2,
        hang_time=1.0,
        tot_duration=5.0,
        id_interval=100.0,
        id_audio_duration=0.5,
        **config_overrides,
    )
    service = RepeaterService(config=config, macros=macros, clock=lambda: clock["now"])
    return service, clock


def test_simulate_cos_updates_state_and_snapshot():
    service, clock = make_service()

    service.simulate_cos(active=True)

    snapshot = service.snapshot()
    assert snapshot.state == RECEIVING
    assert snapshot.ptt_active is True
    assert snapshot.cos_active is True


def test_tick_advances_timers_using_injected_clock():
    service, clock = make_service()
    service.simulate_cos(active=True)
    clock["now"] = 1.0
    service.simulate_cos(active=False)

    clock["now"] = 1.2
    service.tick()
    assert service.snapshot().state == "hang_time"

    clock["now"] = 2.3
    service.tick()
    assert service.snapshot().state == "idle"
    assert service.snapshot().ptt_active is False


def test_simulate_dtmf_dispatches_macro_command():
    macro = Macro(
        pattern="*81",
        description="test macro",
        command="disconnect_all",
        node_id="*",
    )
    service, clock = make_service(macros=[macro])

    service.simulate_dtmf("*")
    service.simulate_dtmf("8")
    service.simulate_dtmf("1")  # should not raise; command has no visible status effect yet


def test_simulate_remote_keyed_tracks_linked_nodes():
    service, clock = make_service()

    service.simulate_remote_keyed("1999", keyed=True)
    assert service.snapshot().linked_nodes == ["1999"]
    assert service.snapshot().state == RECEIVING

    service.simulate_remote_keyed("1999", keyed=False)
    assert service.snapshot().linked_nodes == []


def test_send_link_command_reaches_the_configured_sink():
    macro = Macro(pattern="*81", description="test macro", command="disconnect_all", node_id="1999")
    sent: list[SendLinkCommand] = []
    service, clock = make_service(macros=[macro])
    service.set_link_command_sink(sent.append)

    service.simulate_dtmf("*")
    service.simulate_dtmf("8")
    service.simulate_dtmf("1")

    assert sent == [SendLinkCommand(node_id="1999", command="disconnect_all")]


def test_handle_link_event_tracks_linked_nodes_and_drives_the_state_machine():
    service, clock = make_service()

    service.handle_link_event(LinkStateChanged(node_id="1999", linked=True))
    assert service.snapshot().linked_nodes == ["1999"]

    service.handle_link_event(RemoteKeyed(node_id="1999", keyed=True))
    assert service.snapshot().state == RECEIVING

    service.handle_link_event(RemoteKeyed(node_id="1999", keyed=False))
    service.handle_link_event(LinkStateChanged(node_id="1999", linked=False))
    assert service.snapshot().linked_nodes == []


def test_update_config_takes_effect_on_controller():
    service, clock = make_service()

    new_config = service.update_config(hang_time=42.0)

    assert new_config.hang_time == 42.0
    assert service.config.hang_time == 42.0
    assert service.controller.config.hang_time == 42.0


def test_snapshot_notifies_subscribers():
    import asyncio

    async def run():
        service, clock = make_service()
        queue = service.subscribe()

        service.simulate_cos(active=True)

        snapshot = await queue.get()
        assert snapshot.state == RECEIVING

        service.unsubscribe(queue)

    asyncio.run(run())


def test_snapshot_round_trips_config_and_macros_into_a_fresh_service():
    macro = Macro(pattern="*81", description="disconnect all", command="disconnect_all", node_id="*")
    source, _ = make_service(macros=[macro], callsign="W1AW")

    exported = source.export_snapshot()

    destination, _ = make_service()
    destination.import_snapshot(exported)

    assert destination.config == source.config
    assert destination.list_macros() == source.list_macros()
