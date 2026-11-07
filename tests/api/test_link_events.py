"""Dashboard link-activity ring buffer."""

from controller.events import LinkStateChanged, RemoteKeyed

from api.service.facade import RepeaterService
from api.service.snapshot import LINK_AUDIO_NODE, LINK_RADIO_NODE


def test_link_events_record_connect_and_key():
    service = RepeaterService()
    service.handle_link_event(LinkStateChanged(node_id="2000", linked=True))
    service.handle_link_event(RemoteKeyed(node_id="2000", keyed=True))
    service.handle_link_event(RemoteKeyed(node_id="2000", keyed=False))
    service.handle_link_event(LinkStateChanged(node_id="2000", linked=False))

    events = service.links.recent_events()
    assert [e["kind"] for e in events] == ["disconnected", "unkeyed", "keyed", "connected"]
    assert all(e["node"] == "2000" for e in events)


def test_link_events_skip_allstar_audio_node_but_keep_linked_radio():
    service = RepeaterService()
    service.handle_link_event(RemoteKeyed(node_id=LINK_AUDIO_NODE, keyed=True))
    service.link_radio_keyed(True)
    service.link_radio_keyed(False)

    events = service.links.recent_events()
    assert [e["node"] for e in events] == [LINK_RADIO_NODE, LINK_RADIO_NODE]
    assert [e["kind"] for e in events] == ["unkeyed", "keyed"]
