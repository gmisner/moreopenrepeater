"""Past-net detail, calendar/plans, PDF, attendance, and summary (#317)."""
from __future__ import annotations

import asyncio
from datetime import datetime

from api.net import NetMode
from api.net_pdf import net_pdf
from api.weekly import next_start, starts_between
from controller.state_machine import RepeaterConfig
from fastapi.testclient import TestClient

from api.activity import ActivityRecorder, ActivityStore
from api.app import create_app
from api.assets import AudioAssetStore
from api.service import RepeaterService
from controller.macros import Macro
from pathlib import Path
import tempfile


def run(coro):
    return asyncio.run(coro)


def make_net(config=None, now=1_000_000.0, wall=datetime(2026, 10, 5, 19, 0)):
    clock = {"now": now, "wall": wall}
    service = RepeaterService(config=config or RepeaterConfig(callsign="W1AW"), clock=lambda: 0.0, wall_clock=lambda: clock["wall"])
    service.speak = lambda *_a, **_k: None
    net = NetMode(service, None, None, clock=lambda: clock["now"], wall_clock=lambda: clock["wall"])
    return net, clock


def test_past_net_detail_and_edit_checkins():
    net, _ = make_net()
    run(net.start("ann", name="ARES"))
    checkin = net.add_checkin("W1AW", "ncs", name="Hiram")
    finished = run(net.end("ann"))
    detail = net.find(finished["id"])
    assert len(detail["checkins"]) == 1
    net.update_checkin(checkin["id"], "W1AW", "NCS", name="Hiram Maxim", net_id=finished["id"])
    assert net.find(finished["id"])["checkins"][0]["notes"] == "NCS"
    assert net.find(finished["id"])["checkins"][0]["name"] == "Hiram Maxim"


def test_pdf_starts_with_pdf_header():
    net, _ = make_net(RepeaterConfig(net_name="Tuesday", net_club_name="YCARS"))
    run(net.start("ann"))
    net.add_checkin("W1AW", "")
    blob = net.pdf(net.current["id"])
    assert blob.startswith(b"%PDF-1.4")
    assert b"W1AW" in blob
    assert net_pdf(net.current).startswith(b"%PDF")


def test_attendance_and_summary():
    net, clock = make_net(RepeaterConfig(net_name="Tuesday Net"))
    run(net.start("ann", name="Tuesday Net"))
    net.add_checkin("W1AW", "")
    net.add_checkin("KD2ABC", "")
    run(net.end("ann"))
    clock["now"] += 100
    run(net.start("ann", name="Tuesday Net"))
    net.add_checkin("W1AW", "")
    rows = net.attendance("Tuesday Net")
    assert rows[0] == {"callsign": "W1AW", "count": 2}
    summary = net.summary(0, clock["now"] + 1)
    assert summary["net_count"] == 2 and summary["checkin_count"] == 3
    assert summary["by_name"][0]["name"] == "Tuesday Net"


def test_biweekly_and_monthly_schedules():
    weekly = {"days": [0], "time": "19:00", "minutes": 60, "enabled": True, "every": "weekly"}
    bi = {**weekly, "every": "biweekly", "biweek_parity": datetime(2026, 10, 5).isocalendar()[1] % 2}
    monthly = {"days": [], "time": "19:00", "minutes": 60, "enabled": True, "every": "monthly", "month_day": 5}
    now = datetime(2026, 10, 5, 18, 0)
    assert next_start(weekly, now) == datetime(2026, 10, 5, 19, 0)
    assert next_start(bi, now) == datetime(2026, 10, 5, 19, 0)
    assert next_start(bi, datetime(2026, 10, 5, 19, 1)).isocalendar()[1] % 2 == bi["biweek_parity"]
    assert next_start(monthly, datetime(2026, 10, 1, 12, 0)) == datetime(2026, 10, 5, 19, 0)
    events = starts_between(weekly, datetime(2026, 10, 1), datetime(2026, 10, 15))
    assert datetime(2026, 10, 5, 19, 0) in events and datetime(2026, 10, 12, 19, 0) in events


def test_plan_and_calendar_api():
    service = RepeaterService(
        config=RepeaterConfig(callsign="W1AW", net_name="Club Net"),
        macros=[Macro(pattern="*71", description="net", action="net_start")],
        clock=lambda: 0.0,
        activity=ActivityRecorder(ActivityStore()),
    )
    tmp = Path(tempfile.mkdtemp())
    app = create_app(
        service=service,
        start_background_tick=False,
        assets_store=AudioAssetStore(tmp / "audio"),
        log_path=tmp / "test.log",
    )
    client = TestClient(app)
    with client:
        assert client.post("/api/net/start", json={"name": "ARES"}).status_code == 200
        checkin = client.get("/api/net").json()["current"]["checkins"]
        assert checkin == []
        assert client.post("/api/net/checkins", json={"callsign": "W1AW", "notes": "ncs"}).status_code == 200
        net_id = client.get("/api/net").json()["current"]["id"]
        assert client.post("/api/net/end").status_code == 200

        detail = client.get(f"/api/nets/{net_id}").json()
        assert detail["name"] == "ARES" and detail["checkins"][0]["callsign"] == "W1AW"
        assert (
            client.put(
                f"/api/nets/{net_id}/checkins/{detail['checkins'][0]['id']}",
                json={"callsign": "W1AW", "notes": "NCS", "name": "Hiram"},
            ).status_code
            == 200
        )
        pdf = client.get(f"/api/nets/{net_id}/checkins.pdf")
        assert pdf.status_code == 200 and pdf.headers["content-type"].startswith("application/pdf")
        assert pdf.content.startswith(b"%PDF")

        future = "2099-01-15T19:00:00"
        planned = client.post("/api/nets/plan", json={"name": "Special", "starts_at": future, "minutes": 90})
        assert planned.status_code == 200
        assert planned.json()["planned"][0]["name"] == "Special"
        cal = client.get("/api/nets/calendar", params={"from": "2099-01-01T00:00:00", "to": "2099-02-01T00:00:00"})
        assert cal.status_code == 200
        assert any(event["kind"] == "planned" and event["name"] == "Special" for event in cal.json()["events"])

        att = client.get("/api/nets/attendance", params={"name": "ARES"}).json()
        assert att["rows"][0]["callsign"] == "W1AW"
        summary = client.get("/api/nets/summary", params={"from": 0, "to": 9999999999}).json()
        assert summary["net_count"] >= 1
