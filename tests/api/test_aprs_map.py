import asyncio

import pytest

from api.aprs_map import (
    AprsReceiver,
    StationStore,
    bearing_degrees,
    compass_point,
    distance_km,
    map_center,
    spoken_summary,
)
from controller.state_machine import RepeaterConfig
from link.aprs_packet import parse_packet

HOME = RepeaterConfig(aprs_lat=41.7, aprs_lon=-72.7, aprs_map_enabled=True, callsign="W1AW")


def pos(line):
    p = parse_packet(line)
    assert p is not None
    return p


def test_distance_and_bearing():
    assert distance_km(41.7, -72.7, 41.7, -72.7) == 0
    assert distance_km(0, 0, 0, 1) == pytest.approx(111.19, abs=0.01)
    assert compass_point(bearing_degrees(41.7, -72.7, 42.0, -72.7)) == "north"
    assert compass_point(bearing_degrees(41.7, -72.7, 41.5, -72.9)) == "south west"


def test_map_center_prefers_aprs_position_then_weather():
    assert map_center(RepeaterConfig()) is None
    assert map_center(RepeaterConfig(wx_lat=1.0, wx_lon=2.0)) == (1.0, 2.0)
    assert map_center(RepeaterConfig(wx_lat=1.0, wx_lon=2.0, aprs_lat=3.0, aprs_lon=4.0)) == (3.0, 4.0)


def test_store_keeps_latest_report_and_trail():
    store = StationStore()
    store.record(pos("K1ABC-9>APRS:!4140.00N/07240.00W>090/030 heading east"), at=100)
    store.record(pos("K1ABC-9>APRS:!4140.00N/07240.00W>090/030"), at=110)  # didn't move
    store.record(pos("K1ABC-9>APRS:!4140.00N/07238.00W>090/030"), at=120)

    [station] = store.stations(since=0)
    assert station.lon == pytest.approx(-72.633333, abs=1e-5)
    assert station.packets == 3 and station.first_heard == 100 and station.last_heard == 120
    assert station.comment == "heading east"  # kept when a later report has none
    assert station.category == "mobile"
    assert [t[0] for t in station.trail] == [100, 120]


def test_killed_objects_disappear_and_old_stations_are_pruned():
    store = StationStore()
    store.record(pos("N3TJJ>APRS:;448.225PG*111111z4030.41N/07622.60Wr448.225MHz"), at=100)
    store.record(pos("K1ABC>APRS:!4140.00N/07240.00W-"), at=50)
    assert {s.name for s in store.stations(since=0)} == {"448.225PG", "K1ABC"}
    assert [s.name for s in store.stations(since=60)] == ["448.225PG"]

    store.record(pos("N3TJJ>APRS:;448.225PG_111111z4030.41N/07622.60Wr"), at=130)
    store.prune(older_than=60)
    assert store.stations(since=0) == []


def test_spoken_summary():
    store = StationStore()
    assert spoken_summary([], HOME, now=1000) == "No A P R S stations heard in the last hour."
    store.record(pos("K1ABC-9>APRS:!4145.00N/07242.00W>090/030"), at=900)
    store.record(pos("W2XYZ>APRS:!4200.00N/07300.00W-"), at=950)
    store.record(pos("OLD>APRS:!4142.00N/07242.00W-"), at=-5000)
    text = spoken_summary(store.stations(since=-10000), HOME, now=1000)
    assert text == "2 A P R S stations heard in the last hour. 1 is mobile. The closest is K 1 A B C, 3 miles north."

    metric = RepeaterConfig(**{**HOME.__dict__, "distance_units": "km", "id_phonetic": True})
    assert "Kilo one Alpha Bravo Charlie, 6 kilometers north" in spoken_summary(store.stations(0), metric, now=1000)


class FakeFeed:
    """Stands in for APRS-IS: records logins and serves queued lines."""

    def __init__(self):
        self.logins = []
        self.queue: asyncio.Queue = asyncio.Queue()

    async def lines(self, host, port, login, filter_):
        self.logins.append((host, port, login, filter_))
        while True:
            line = await self.queue.get()
            if isinstance(line, Exception):
                raise line
            yield line


async def settle():
    for _ in range(20):
        await asyncio.sleep(0)


def run_receiver(scenario, config):
    async def main():
        store = StationStore()
        feed = FakeFeed()
        state = {"config": config, "now": 1000.0}
        receiver = AprsReceiver(store, lambda: state["config"], lines=feed.lines, clock=lambda: state["now"])
        task = asyncio.create_task(receiver.run())
        try:
            await scenario(receiver, feed, state, store)
        finally:
            task.cancel()

    asyncio.run(main())


def test_receiver_logs_in_with_range_filter_and_records_positions():
    async def scenario(receiver, feed, state, store):
        await settle()
        assert feed.logins == [("rotate.aprs2.net", 14580, "W1AW", "r/41.700/-72.700/50")]
        await feed.queue.put("# logresp W1AW unverified, server T2TEST\r\n")
        await feed.queue.put("K1ABC-9>APRS,qAR:!4140.00N/07240.00W>\r\n")
        state["now"] += 1  # past the write batching interval
        await feed.queue.put("# heartbeat\r\n")
        await settle()
        await asyncio.sleep(0.05)  # the batch is written in a thread
        assert receiver.status.connected and receiver.status.packets == 1
        assert [s.name for s in store.stations(0)] == ["K1ABC-9"]

    run_receiver(scenario, HOME)


def test_receiver_reconnects_when_the_filter_changes_and_stops_when_disabled():
    async def scenario(receiver, feed, state, store):
        await settle()
        state["config"] = RepeaterConfig(**{**HOME.__dict__, "aprs_map_radius_km": 100.0})
        receiver.settings_changed()
        await feed.queue.put("# heartbeat\r\n")
        await settle()
        assert [login[3] for login in feed.logins] == ["r/41.700/-72.700/50", "r/41.700/-72.700/100"]

        state["config"] = RepeaterConfig(**{**HOME.__dict__, "aprs_map_enabled": False})
        receiver.settings_changed()
        await feed.queue.put("# heartbeat\r\n")
        await settle()
        assert len(feed.logins) == 2 and not receiver.status.connected

    run_receiver(scenario, HOME)


def test_receiver_uses_fallback_login_and_reports_refusal():
    async def scenario(receiver, feed, state, store):
        await settle()
        assert feed.logins[0][2] == "MOREOPEN"
        await feed.queue.put("# Login by user not allowed\r\n")
        await settle()
        assert receiver.status.connected is False and "refused" in receiver.status.error

    run_receiver(scenario, RepeaterConfig(aprs_lat=41.7, aprs_lon=-72.7, aprs_map_enabled=True))


def test_receiver_waits_without_a_location():
    async def scenario(receiver, feed, state, store):
        await settle()
        assert feed.logins == []

    run_receiver(scenario, RepeaterConfig(aprs_map_enabled=True))


def test_stations_endpoint_and_dtmf_summary(tmp_path):
    import time

    from fastapi.testclient import TestClient

    from api.app import create_app
    from api.service import RepeaterService
    from controller.events import RunAction

    store = StationStore()
    store.record(pos("K1ABC-9>APRS:!4145.00N/07242.00W>090/030"), at=time.time())
    store.record(pos("K1OLD>APRS:!4145.00N/07242.00W-"), at=time.time() - 5 * 3600)
    service = RepeaterService(config=HOME)
    app = create_app(service=service, start_background_tick=False, log_path=tmp_path / "t.log", aprs_stations=store)
    body = TestClient(app).get("/api/aprs/stations").json()

    assert body["enabled"] and body["center"] == [41.7, -72.7] and body["distance_units"] == "mi"
    assert body["connected"] is False
    [station] = body["stations"]  # K1OLD is outside the 3 hour window
    assert station["name"] == "K1ABC-9" and station["category"] == "mobile"
    assert station["distance_km"] == pytest.approx(5.56, abs=0.01) and station["bearing"] == 0
    assert station["trail"] == [[41.75, -72.7]]

    service._run_action(RunAction("aprs"))
    [spoken] = service.controller.queued_announcements
    assert spoken.startswith("tts:1 A P R S station heard in the last hour. 1 is mobile. The closest is K 1 A B C, 3 miles north.")
