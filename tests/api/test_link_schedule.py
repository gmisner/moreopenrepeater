import asyncio
from datetime import datetime

from api.link_schedule import LinkScheduler, last_start, next_start
from api.links import LinkError

# 2026-09-29 is a Tuesday (weekday 1).
NET = {"node": "2000", "name": "Tuesday net", "days": [1], "time": "19:00", "minutes": 60, "monitor": False, "enabled": True}


def at(hour, minute=0, day=29):
    return datetime(2026, 9, day, hour, minute)


class FakeLinks:
    def __init__(self):
        self.up = {}
        self.calls = []
        self.broken = False

    async def linked(self):
        if self.broken:
            raise LinkError("not connected")
        return dict(self.up)

    async def connect(self, node, monitor):
        self.calls.append(("connect", node, monitor))
        self.up[node] = ("monitor" if monitor else "transceive", False)

    async def disconnect(self, node):
        self.calls.append(("disconnect", node))
        self.up.pop(node, None)


def run(scheduler, *times):
    async def scenario():
        for now in times:
            await scheduler.tick(now)

    asyncio.run(scenario())


def test_start_times():
    assert last_start(NET, at(19, 30)) == at(19)
    assert last_start(NET, at(18)) == at(19, day=22)
    assert next_start(NET, at(19)) == at(19, day=6).replace(month=10)
    assert next_start(NET, at(8)) == at(19)
    assert next_start({**NET, "enabled": False}, at(8)) is None


def test_links_for_the_window_then_drops_it():
    links = FakeLinks()
    scheduler = LinkScheduler(links, lambda: [NET])
    run(scheduler, at(18, 59), at(19), at(19, 0))
    assert links.calls == [("connect", "2000", False)]
    assert scheduler.until("2000") == at(20)
    assert scheduler.status(at(19, 30)) == [{"node": "2000", "next_start": at(19, day=6).replace(month=10), "until": at(20)}]
    run(scheduler, at(20))
    assert links.calls[-1] == ("disconnect", "2000") and scheduler.until("2000") is None


def test_a_link_that_was_already_up_is_left_alone():
    links = FakeLinks()
    links.up["2000"] = ("transceive", False)
    scheduler = LinkScheduler(links, lambda: [NET])
    run(scheduler, at(19), at(20, 1))
    assert links.calls == []


def test_disconnecting_by_hand_keeps_it_down():
    links = FakeLinks()
    scheduler = LinkScheduler(links, lambda: [NET])
    run(scheduler, at(19))
    links.up.clear()
    run(scheduler, at(19, 10), at(20))
    assert links.calls == [("connect", "2000", False)]


def test_retries_until_app_rpt_is_back_and_restarts_mid_window():
    links = FakeLinks()
    links.broken = True
    scheduler = LinkScheduler(links, lambda: [NET])
    run(scheduler, at(19))
    links.broken = False
    run(scheduler, at(19, 20))
    assert links.calls == [("connect", "2000", False)]
    restarted = FakeLinks()
    run(LinkScheduler(restarted, lambda: [NET]), at(19, 30))
    assert restarted.calls == [("connect", "2000", False)]
    late = FakeLinks()
    run(LinkScheduler(late, lambda: [NET]), at(20, 1))
    assert late.calls == []


def test_stay_linked_schedules_only_connect_near_the_start():
    stay = {**NET, "minutes": 0, "monitor": True}
    links = FakeLinks()
    run(LinkScheduler(links, lambda: [stay]), at(19, 6))
    assert links.calls == []
    run(LinkScheduler(links, lambda: [stay]), at(19, 2), at(23))
    assert links.calls == [("connect", "2000", True)]


def test_paused_and_other_days():
    links = FakeLinks()
    run(LinkScheduler(links, lambda: [{**NET, "enabled": False}, {**NET, "node": "2001", "days": [2]}]), at(19, 1))
    assert links.calls == []
