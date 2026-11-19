"""The AllStar link service's bridge between app_rpt and the repeater (#221, #229)."""
import asyncio

import pytest

from api.link_bridge import LinkBridge
from api.runtime import LinkSettings
from controller.events import LinkStateChanged, RemoteKeyed

SETTINGS = LinkSettings("10.1.2.3", 5038, "admin", "ami-password-do-not-log", "41521")
SECRET = "private-detail"


class FakeService:
    def __init__(self, held=""):
        self.calls = []
        self.held = held

    def link_sync(self, nodes):
        self.calls.append(("sync", dict(nodes)))

    def link_event(self, event):
        self.calls.append(("event", event))

    def held_reason(self, feature):
        return self.held


class FakeClient:
    """One instance per connection; `plan` says what each connection does."""

    instances = []
    plan = []  # per connection: {"connect": exception | None, "links": dict | exception, "events": [...], "end": exception | None}

    def __init__(self, host, port, username, secret, node):
        self.args = (host, port, username, secret, node)
        self.index = len(FakeClient.instances)
        FakeClient.instances.append(self)
        self.step = FakeClient.plan[min(self.index, len(FakeClient.plan) - 1)] if FakeClient.plan else {}
        self.sent = []
        self.remembered = None
        self.closed = False

    async def connect(self):
        if self.step.get("connect"):
            raise self.step["connect"]

    async def links(self):
        value = self.step.get("links", {})
        if isinstance(value, Exception):
            raise value
        if value == "hang":
            await asyncio.sleep(3600)
        return {node: ("transceive", keyed) for node, keyed in value.items()}

    def remember_links(self, links):
        self.remembered = dict(links)

    async def events(self):
        for event in self.step.get("events", []):
            yield event
        if self.step.get("end"):
            raise self.step["end"]
        await asyncio.sleep(3600)

    async def send_macro_command(self, node_id, command):
        self.sent.append((node_id, command))

    async def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def fresh():
    FakeClient.instances, FakeClient.plan = [], []


def make(service, **kwargs):
    kwargs.setdefault("client_factory", FakeClient)
    kwargs.setdefault("reconnect_delay", 0.02)
    return LinkBridge(service, SETTINGS, **kwargs)


async def until(condition, timeout=3.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not condition():
        assert loop.time() < deadline, "timed out"
        await asyncio.sleep(0.01)


def running(bridge, scenario):
    async def go():
        task = asyncio.create_task(bridge.run())
        try:
            return await scenario()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    return asyncio.run(go())


def test_it_gives_the_core_the_picture_at_connect_then_each_change():
    FakeClient.plan = [{"links": {"41520": True}, "events": [RemoteKeyed(node_id="41520", keyed=False), LinkStateChanged(node_id="53127", linked=True)]}]
    service, seen = FakeService(), []
    bridge = make(service, on_client=seen.append)

    async def scenario():
        await until(lambda: len(service.calls) >= 3)

    running(bridge, scenario)
    assert service.calls[:3] == [
        ("sync", {"41520": True}), ("event", RemoteKeyed(node_id="41520", keyed=False)), ("event", LinkStateChanged(node_id="53127", linked=True)),
    ]
    assert FakeClient.instances[0].remembered == {"41520": True}  # so the events that follow are changes, not the picture again
    assert FakeClient.instances[0].args == ("10.1.2.3", 5038, "admin", "ami-password-do-not-log", "41521")
    assert seen[0] is FakeClient.instances[0]  # the dashboard's link controls get the same connection


def test_losing_app_rpt_tells_the_core_there_are_no_links_and_it_tries_again(structured_events):
    FakeClient.plan = [{"links": {"41520": True}, "end": ConnectionError(SECRET)}, {"links": {}}]
    service, seen = FakeService(), []
    bridge = make(service, on_client=seen.append)

    async def scenario():
        await until(lambda: len(FakeClient.instances) >= 2)
        await until(lambda: ("sync", {}) in service.calls[2:])

    running(bridge, scenario)
    assert service.calls[0] == ("sync", {"41520": True}) and service.calls[1] == ("sync", {})  # a keyed node cannot stay keyed
    assert FakeClient.instances[0].closed
    assert seen[:3] == [FakeClient.instances[0], None, FakeClient.instances[1]]  # not connected, in between
    lost = structured_events.named("link.connection_lost")[0]
    assert lost.error_code == "link.ami_connection" and SECRET not in lost.model_dump_json()


def test_an_app_rpt_that_cannot_be_reached_is_reported_to_the_core_as_no_links_and_retried():
    FakeClient.plan = [{"connect": OSError(SECRET)}, {"connect": OSError(SECRET)}, {"links": {"41520": False}}]
    service = FakeService()
    bridge = make(service)

    async def scenario():
        await until(lambda: ("sync", {"41520": False}) in service.calls)

    running(bridge, scenario)
    assert len(FakeClient.instances) >= 3
    assert service.calls[0] == ("sync", {}) and service.calls[1] == ("sync", {})  # said at each failure; the core dedupes


@pytest.mark.parametrize("links", [ValueError("the node is not chosen yet"), OSError("died"), "hang"])
def test_a_snapshot_that_cannot_be_had_is_no_links_not_a_crash(links, monkeypatch):
    from api import link_bridge

    monkeypatch.setattr(link_bridge, "SNAPSHOT_TIMEOUT_SECONDS", 0.05)
    FakeClient.plan = [{"links": links, "events": [LinkStateChanged(node_id="41520", linked=True)]}]
    service = FakeService()
    bridge = make(service)

    async def scenario():
        await until(lambda: len(service.calls) >= 2)

    running(bridge, scenario)
    assert service.calls[0] == ("sync", {}) and service.calls[1] == ("event", LinkStateChanged(node_id="41520", linked=True))


def test_a_macros_link_command_goes_to_app_rpt_over_the_connection():
    FakeClient.plan = [{}]
    service = FakeService()
    bridge = make(service)

    async def scenario():
        await until(lambda: bridge.client is not None)
        await bridge.command("41520", "*3")

    running(bridge, scenario)
    assert FakeClient.instances[0].sent == [("41520", "*3")]


def test_a_link_command_with_no_connection_is_dropped_and_said_so_without_the_node(structured_events):
    bridge = make(FakeService())
    assert bridge.command("41520", "*3") is None
    [dropped] = structured_events.named("link.command_dropped")
    assert dropped.severity == 30 and dropped.error_code == "link.not_connected" and "41520" not in dropped.model_dump_json()


def test_a_node_that_links_in_from_outside_while_linking_is_held_is_dropped():
    FakeClient.plan = [{"events": [
        LinkStateChanged(node_id="53127", linked=True), RemoteKeyed(node_id="53127", keyed=True), LinkStateChanged(node_id="60001", linked=False),
    ]}]
    service, dropped = FakeService(held="GMRS mode"), []

    async def drop(node):
        dropped.append(node)

    bridge = make(service, on_linked=drop)

    async def scenario():
        await until(lambda: len(service.calls) >= 4)
        await asyncio.sleep(0.05)

    running(bridge, scenario)
    assert dropped == ["53127"]  # only the one that linked, not the key-up or the unlink


def test_nothing_is_dropped_when_linking_is_allowed():
    FakeClient.plan = [{"events": [LinkStateChanged(node_id="53127", linked=True)]}]
    service, dropped = FakeService(held=""), []

    async def drop(node):
        dropped.append(node)

    bridge = make(service, on_linked=drop)

    async def scenario():
        await until(lambda: len(service.calls) >= 2)
        await asyncio.sleep(0.05)

    running(bridge, scenario)
    assert dropped == []


def test_the_node_comes_from_the_settings_or_failing_that_the_one_the_allstar_node_chose():
    FakeClient.plan = [{}]
    bridge = LinkBridge(FakeService(), LinkSettings("h", 1, "u", "s", ""), node=lambda: "1999", client_factory=FakeClient, reconnect_delay=0.02)

    async def scenario():
        await until(lambda: FakeClient.instances)

    running(bridge, scenario)
    assert FakeClient.instances[0].args[4] == "1999"


def test_the_connecting_event_does_not_carry_the_password(structured_events):
    FakeClient.plan = [{}]
    bridge = make(FakeService())

    async def scenario():
        await until(lambda: structured_events.named("link.connected"))

    running(bridge, scenario)
    connected = structured_events.named("link.connected")[0]
    assert connected.context == {"host": "10.1.2.3", "port": 5038, "node": "41521"} and "ami-password" not in connected.model_dump_json()
