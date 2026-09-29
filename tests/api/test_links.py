import asyncio
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.assets import AudioAssetStore
from api.links import LinkControl, LinkError, echolink_number
from api.node_directory import MAX_AGE_SECONDS, NodeDirectory, parse
from api.service import RepeaterService
from controller.state_machine import RepeaterConfig

NODE_LIST = b"2000|WB6NIL|ASL Public Hub|Los Angeles, CA\n1998|W1AW|Test|\nnot a node\n27339|K6XYZ\n"


class FakeClient:
    def __init__(self):
        self.local_node_id = ""
        self.calls = []
        self.linked = {"2000": ("transceive", True)}
        self.lookups = 0

    async def links(self):
        return self.linked

    async def connect_node(self, node, monitor=False):
        self.calls.append(("connect", node, monitor))

    async def disconnect_node(self, node):
        self.calls.append(("disconnect", node))

    async def disconnect_all(self):
        self.calls.append(("disconnect all",))

    async def echolink_callsign(self, number):
        self.lookups += 1
        return "*ECHOTEST*" if number == "9999" else None


def directory():
    nodes = NodeDirectory(None, download=lambda: NODE_LIST)
    nodes.refresh()
    return nodes


def test_parse_node_list():
    assert parse(NODE_LIST.decode()) == {
        "2000": {"callsign": "WB6NIL", "description": "ASL Public Hub", "location": "Los Angeles, CA"},
        "1998": {"callsign": "W1AW", "description": "Test", "location": ""},
        "27339": {"callsign": "K6XYZ", "description": "", "location": ""},
    }


def test_node_list_is_saved_and_reloaded(tmp_path):
    now = [1_000_000.0]
    path = tmp_path / "data" / "allstar-nodes.txt"
    nodes = NodeDirectory(path, download=lambda: NODE_LIST, clock=lambda: now[0])
    nodes.load()
    assert nodes.stale() and len(nodes) == 0
    nodes.refresh()
    assert path.read_bytes() == NODE_LIST
    assert not nodes.stale()
    now[0] += MAX_AGE_SECONDS + 1
    assert nodes.stale()

    reloaded = NodeDirectory(path)
    reloaded.load()
    assert reloaded.lookup("2000")["callsign"] == "WB6NIL"


def test_an_empty_download_keeps_the_old_list(tmp_path):
    path = tmp_path / "allstar-nodes.txt"
    path.write_bytes(NODE_LIST)
    nodes = NodeDirectory(path, download=lambda: b"<html>maintenance</html>")
    nodes.load()
    with pytest.raises(ValueError):
        nodes.refresh()
    assert len(nodes) == 3 and path.read_bytes() == NODE_LIST


def test_echolink_numbers():
    assert echolink_number("3009999") == "9999"
    assert echolink_number("3123456") == "123456"
    assert echolink_number("2000") is None
    assert echolink_number("4009999") is None


def test_status_names_callsigns_and_echolink():
    async def scenario():
        service = RepeaterService(config=RepeaterConfig(link_favorites=[
            {"node": "2000", "name": "Hub", "monitor": True},
            {"node": "3009999", "name": "", "monitor": False},
        ]))
        links = LinkControl(service, directory(), lambda: "1999")
        client = FakeClient()
        links.client = client
        status = await links.status()
        assert status["available"] and status["node"] == "1999" and status["error"] is None
        assert status["links"] == [{
            "node": "2000", "kind": "allstar", "callsign": "WB6NIL", "description": "ASL Public Hub",
            "location": "Los Angeles, CA", "mode": "transceive", "keyed": True, "name": "Hub",
        }]
        echo = status["favorites"][1]
        assert (echo["kind"], echo["callsign"], echo["description"]) == ("echolink", "*ECHOTEST*", "EchoLink 9999")
        await links.status()
        assert client.lookups == 1

        await links.connect("2001", True)
        await links.disconnect("2000")
        await links.disconnect_all()
        assert client.calls == [("connect", "2001", True), ("disconnect", "2000"), ("disconnect all",)]

    asyncio.run(scenario())


def test_status_without_app_rpt_uses_the_known_links():
    async def scenario():
        service = RepeaterService()
        links = LinkControl(service, NodeDirectory(None))
        status = await links.status()
        assert not status["available"] and status["links"] == []
        with pytest.raises(LinkError, match="isn't connected"):
            await links.connect("2000", False)
        links.client = FakeClient()
        with pytest.raises(LinkError, match="node first"):
            await links.disconnect_all()

    asyncio.run(scenario())


def test_app_rpt_failures_are_reported():
    class Broken(FakeClient):
        async def links(self):
            raise ConnectionError("AMI closed")

        async def connect_node(self, node, monitor=False):
            raise ConnectionError("AMI closed")

    async def scenario():
        links = LinkControl(RepeaterService(), NodeDirectory(None), lambda: "1999")
        links.client = Broken()
        assert "AMI closed" in (await links.status())["error"]
        with pytest.raises(LinkError, match="didn't take"):
            await links.connect("2000", False)

    asyncio.run(scenario())


def test_routes(tmp_path):
    service = RepeaterService()
    links = LinkControl(service, directory(), lambda: "1999")
    app = create_app(
        service=service,
        start_background_tick=False,
        assets_store=AudioAssetStore(Path(tempfile.mkdtemp()) / "audio"),
        log_path=tmp_path / "test.log",
        links=links,
    )
    with TestClient(app) as client:
        assert client.post("/api/links", json={"node": "2000"}).status_code == 409
        fake = FakeClient()
        links.client = fake
        assert client.post("/api/links", json={"node": "2000; core stop"}).status_code == 422
        assert client.delete("/api/links/20x0").status_code == 422
        assert client.post("/api/links", json={"node": "2001", "monitor": True}).status_code == 200
        assert client.delete("/api/links/2001").status_code == 200
        assert client.delete("/api/links").status_code == 200
        assert fake.calls == [("connect", "2001", True), ("disconnect", "2001"), ("disconnect all",)]
        assert client.get("/api/links").json()["links"][0]["callsign"] == "WB6NIL"

        assert client.put("/api/config", json={"link_favorites": [{"node": "abc"}]}).status_code == 422
        assert client.put("/api/config", json={"link_favorites": [{"node": "2000", "name": "x" * 41}]}).status_code == 422
        config = client.put("/api/config", json={"link_favorites": [{"node": "2000", "name": "Hub"}]}).json()
        assert config["link_favorites"] == [{"node": "2000", "name": "Hub", "monitor": False}]
        assert client.get("/api/links").json()["favorites"][0]["name"] == "Hub"
