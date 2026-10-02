import asyncio
import csv
import io
import tempfile
from datetime import datetime
from pathlib import Path

from fastapi.testclient import TestClient

from api.activity import ActivityRecorder, ActivityStore
from api.app import create_app
from api.assets import AudioAssetStore
from api.links import LinkError
from api.net import NetMode
from api.persistence import StateStore
from api.service import RepeaterService
from controller.announcements import Announcement
from controller.macros import Macro
from controller.state_machine import RepeaterConfig


class FakeLinks:
    def __init__(self, linked=(), fail=False):
        self.nodes = set(linked)
        self.fail = fail
        self.calls = []

    async def linked(self):
        if self.fail:
            raise LinkError("The repeater isn't connected to AllStarLink.")
        return {node: ("transceive", False) for node in self.nodes}

    async def connect(self, node, monitor):
        self.calls.append(("connect", node, monitor))
        self.nodes.add(node)

    async def disconnect(self, node):
        self.calls.append(("disconnect", node))
        self.nodes.discard(node)

    async def disconnect_all(self):
        if self.fail:
            raise LinkError("The repeater isn't connected to AllStarLink.")
        self.calls.append(("disconnect_all",))
        self.nodes.clear()


def make_net(config=None, links=None, store=None, now=1_000_000.0, wall=datetime(2026, 10, 5, 19, 0)):
    clock = {"now": now, "wall": wall}
    service = RepeaterService(config=config or RepeaterConfig(callsign="W1AW"), clock=lambda: 0.0, wall_clock=lambda: clock["wall"])
    spoken = []
    service.speak = spoken.append
    net = NetMode(service, links, store, clock=lambda: clock["now"], wall_clock=lambda: clock["wall"])
    return net, service, clock, spoken


def run(coro):
    return asyncio.run(coro)


def test_a_net_applies_its_settings_and_puts_them_back():
    config = RepeaterConfig(
        tot_duration=180, hang_time=3, net_tot_duration=600, net_hang_time=0.5, net_courtesy_tone_style="chirp",
        autopatch_enabled=True, courtesy_tone_link_style="triple",
    )
    net, service, _, _ = make_net(config)

    run(net.start("ann"))
    effective = service.config
    assert (effective.tot_duration, effective.hang_time) == (600, 0.5)
    assert effective.courtesy_tone_style == "chirp" and effective.courtesy_tone_link_style == "same"
    assert effective.autopatch_enabled is False
    assert service.held_reason("autopatch") == "The phone patch is off until the net ends."
    assert service.saved_config == config  # nothing saved changes
    assert service.snapshot().net_active is True

    run(net.end("ann"))
    assert service.config == config and service.snapshot().net_active is False


def test_settings_saved_during_a_net_are_kept_after_it():
    net, service, _, _ = make_net(RepeaterConfig(tot_duration=180, net_tot_duration=600))
    run(net.start("ann"))
    service.update_config(tot_duration=240)
    assert service.config.tot_duration == 600
    run(net.end("ann"))
    assert service.config.tot_duration == 240
    assert service.export_snapshot()["config"]["tot_duration"] == 240


def test_check_ins_and_csv():
    net, _, clock, _ = make_net(RepeaterConfig(net_name="Tuesday Net"))
    run(net.start("ann"))
    first = net.add_checkin(" kd2abc ", "mobile")
    clock["now"] += 60
    second = net.add_checkin("N0CALL", "=HYPERLINK(1)")
    net.update_checkin(first["id"], "KD2ABC", "mobile, short time")
    net.delete_checkin(second["id"])
    net.add_checkin("W1AW", "")
    finished = run(net.end("ann"))

    rows = list(csv.reader(io.StringIO(net.csv(finished["id"]))))
    assert rows[0] == ["Net", "Callsign", "Time", "Notes"]
    assert [r[1] for r in rows[1:]] == ["KD2ABC", "W1AW"]
    assert rows[1][0] == "Tuesday Net" and rows[1][3] == "mobile, short time"
    assert net.status()["past"][0]["checkin_count"] == 2


def test_a_formula_in_the_csv_is_defused():
    net, _, _, _ = make_net()
    run(net.start("ann", name="=cmd"))
    net.add_checkin("W1AW", "@SUM(A1)")
    rows = list(csv.reader(io.StringIO(net.csv(net.current["id"]))))
    assert rows[1][0] == "'=cmd" and rows[1][3] == "'@SUM(A1)"


def test_announcements_wait_for_the_end_of_the_net():
    net, service, clock, _ = make_net()
    service.save_announcement(Announcement(id="a", name="Club meeting", message="Meeting Thursday", every_minutes=60))
    run(net.start("ann"))
    clock["wall"] = datetime(2026, 10, 5, 21, 0)
    assert service.due_announcement_clips() == []
    run(net.end("ann"))
    assert service.due_announcement_clips() == ["tts:Meeting Thursday"]


def test_links_for_the_net():
    links = FakeLinks(linked={"2000"})
    net, _, _, _ = make_net(RepeaterConfig(net_links="disconnect"), links)
    run(net.start("ann"))
    assert links.calls == [("disconnect_all",)]

    links = FakeLinks(linked={"2000"})
    net, _, _, _ = make_net(RepeaterConfig(net_links="connect", net_link_node="41520"), links)
    run(net.start("ann"))
    run(net.end("ann"))
    assert links.calls == [("connect", "41520", False), ("disconnect", "41520")]

    links = FakeLinks(linked={"41520"})  # already linked: left linked after the net
    net, _, _, _ = make_net(RepeaterConfig(net_links="connect", net_link_node="41520"), links)
    run(net.start("ann"))
    run(net.end("ann"))
    assert links.calls == []

    net, _, _, _ = make_net(RepeaterConfig(net_links="disconnect"), FakeLinks(fail=True))
    run(net.start("ann"))
    assert "AllStarLink" in net.current["link_error"]


def test_start_and_end_speech():
    net, _, _, spoken = make_net(RepeaterConfig(net_start_say="The {name} is now in session", net_end_say=""))
    run(net.start("ann", "ARES net"))
    run(net.end("ann"))
    assert spoken == ["tts:The ARES net is now in session"]


def test_a_net_ends_by_itself():
    net, service, clock, _ = make_net(RepeaterConfig(net_max_minutes=90))
    run(net.start("ann"))
    clock["now"] += 89 * 60
    run(net.tick())
    assert net.current is not None
    clock["now"] += 2 * 60
    run(net.tick())
    assert net.current is None and net.past[0]["ended_by"] == "time limit"
    assert service.net_active is False


def test_scheduled_nets():
    schedule = {"days": [0], "time": "19:00", "minutes": 60, "enabled": True}  # Mondays
    net, _, clock, _ = make_net(RepeaterConfig(net_schedules=[schedule]), wall=datetime(2026, 10, 5, 18, 59))
    assert net.status()["next_scheduled"] == datetime(2026, 10, 5, 19, 0)
    run(net.tick())
    assert net.current is None

    clock["wall"] = datetime(2026, 10, 5, 19, 0, 10)
    run(net.tick())
    assert net.current["started_by"] == "schedule"
    assert net.current["ends_at"] == clock["now"] + 60 * 60 - 10

    run(net.end("ann"))  # ended early by hand: the schedule doesn't start it again
    clock["wall"] = datetime(2026, 10, 5, 19, 30)
    run(net.tick())
    assert net.current is None


def test_a_running_net_survives_a_restart(tmp_path):
    store = StateStore(tmp_path / "nets.json")
    net, _, _, _ = make_net(store=store)
    run(net.start("ann"))
    net.add_checkin("W1AW", "")

    net, service, _, _ = make_net(store=store)
    assert service.net_active is True
    assert [c["callsign"] for c in net.current["checkins"]] == ["W1AW"]


def make_client(config=None, links=None):
    service = RepeaterService(
        config=config or RepeaterConfig(callsign="W1AW"),
        macros=[Macro(pattern="*71", description="net", action="net_start"), Macro(pattern="*70", description="end", action="net_end")],
        clock=lambda: 0.0,
        activity=ActivityRecorder(ActivityStore()),
    )
    tmp = Path(tempfile.mkdtemp())
    app = create_app(
        service=service, start_background_tick=False, assets_store=AudioAssetStore(tmp / "audio"), log_path=tmp / "test.log",
        links=links,
    )
    return TestClient(app), service


def test_net_api():
    client, service = make_client()
    with client:
        assert client.get("/api/net").json()["current"] is None
        assert client.post("/api/net/end").status_code == 409

        body = client.post("/api/net/start", json={"name": "ARES net"}).json()
        assert body["current"]["name"] == "ARES net" and body["current"]["started_by"] == "local"
        assert client.get("/api/status").json()["net_active"] is True
        assert client.post("/api/net/start", json={}).status_code == 409

        assert client.post("/api/net/checkins", json={"callsign": "w1aw", "notes": "net control"}).status_code == 200
        assert client.post("/api/net/checkins", json={"callsign": "not a call!"}).status_code == 422
        checkin = client.get("/api/net").json()["current"]["checkins"][0]
        assert checkin["callsign"] == "W1AW"
        assert client.put(f"/api/net/checkins/{checkin['id']}", json={"callsign": "W1AW", "notes": "NCS"}).status_code == 200
        assert client.put("/api/net/checkins/nope", json={"callsign": "W1AW"}).status_code == 404

        net_id = client.get("/api/net").json()["current"]["id"]
        assert client.delete(f"/api/nets/{net_id}").status_code == 409
        past = client.post("/api/net/end").json()["past"]
        assert past[0]["checkin_count"] == 1 and past[0]["ended_by"] == "local"

        response = client.get(f"/api/nets/{net_id}/checkins.csv")
        assert response.headers["content-type"].startswith("text/csv")
        assert 'filename="ares-net-' in response.headers["content-disposition"]
        assert "W1AW" in response.text and "NCS" in response.text

        assert client.delete(f"/api/nets/{net_id}").json()["past"] == []


def test_net_over_dtmf():
    client, service = make_client()
    with client:
        for digit in "*71":
            client.post("/api/simulate/dtmf", json={"digit": digit})
        assert client.get("/api/net").json()["current"]["started_by"] == "DTMF"
        for digit in "*70":
            client.post("/api/simulate/dtmf", json={"digit": digit})
        assert client.get("/api/net").json()["current"] is None


def test_config_api_shows_saved_settings_during_a_net():
    client, service = make_client(RepeaterConfig(tot_duration=180, net_tot_duration=600))
    with client:
        client.post("/api/net/start", json={})
        assert client.get("/api/config").json()["tot_duration"] == 180
        assert service.config.tot_duration == 600


def test_net_link_needs_a_node():
    client, _ = make_client()
    assert client.put("/api/config", json={"net_links": "connect", "net_link_node": ""}).status_code == 422
    assert client.put("/api/config", json={"net_links": "connect", "net_link_node": "41520"}).status_code == 200


def test_net_preview_uses_the_net_tone():
    client, _ = make_client(RepeaterConfig(courtesy_tone_style="beep", net_courtesy_tone_style="chirp"))
    normal = client.post("/api/audio/preview", json={"clip": "courtesy_tone"}).content
    during = client.post("/api/audio/preview", json={"clip": "courtesy_tone", "net": True}).content
    assert normal != during

