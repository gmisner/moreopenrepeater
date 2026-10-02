"""Net mode: one switch that sets the repeater up for a net, and the net's
check-in log.

While a net runs, `controller.modes` applies the net settings (timeout,
hang time, courtesy tone, the phone patch held) over the saved ones, and
scheduled announcements wait until it's over. Starting it can also drop all
links or link a net's hub, and ending it drops the link it made. A net starts
from the dashboard, a DTMF macro or a weekly schedule, and ends from any of
those or after `net_max_minutes`, so it can't be left on.

Net control logs check-ins (callsign, time, notes); the running net and the
last MAX_NETS are kept in a JSON file, and each exports as CSV.
"""
from __future__ import annotations

import asyncio
import csv
import io
import logging
import time
import uuid
from datetime import datetime, timedelta
from typing import Callable, Optional

from playout.renderer import TTS_PREFIX

from .links import LinkControl, LinkError
from .persistence import StateStore
from .service import RepeaterService
from .weekly import last_start, next_start, window_end

_logger = logging.getLogger("moreopenrepeater.net")

MAX_NETS = 50
MAX_CHECKINS = 500
POLL_SECONDS = 15
SCHEDULE_GRACE = timedelta(minutes=5)  # a schedule without an end only starts the net this soon after its time


class NetError(Exception):
    pass


def _cell(text: str) -> str:
    """A leading = + - @ would make a spreadsheet run the cell as a formula."""
    return "'" + text if text[:1] in ("=", "+", "-", "@") else text


def _summary(net: dict) -> dict:
    return {k: v for k, v in net.items() if k != "checkins"} | {"checkin_count": len(net.get("checkins", []))}


class NetMode:
    """`store=None` keeps nets in memory (tests)."""

    def __init__(
        self,
        service: RepeaterService,
        links: Optional[LinkControl],
        store: Optional[StateStore],
        clock: Callable[[], float] = time.time,
        wall_clock: Callable[[], datetime] = datetime.now,
    ) -> None:
        self._service = service
        self._links = links
        self._store = store
        self._clock = clock
        self._wall_clock = wall_clock
        # Starts and ends nobody asked for over the API (that's audited already).
        self.audit_hook: Callable[[str, str, str], None] = lambda actor, action, detail: None
        data = (store.load() if store is not None else None) or {}
        self.current: Optional[dict] = data.get("current")
        self.past: list[dict] = data.get("past", [])
        service.net_hook = self._dtmf
        if self.current is not None:  # a net that was running when the service stopped
            service.set_net_active(True)

    def _save(self) -> None:
        if self._store is not None:
            self._store.save({"current": self.current, "past": self.past})

    def status(self) -> dict:
        now = self._wall_clock()
        starts = [s for s in (next_start(schedule, now) for schedule in self._service.saved_config.net_schedules) if s]
        return {
            "current": self.current,
            "past": [_summary(net) for net in self.past],
            "next_scheduled": min(starts) if starts else None,
        }

    # -- starting and ending --------------------------------------------------

    async def start(self, actor: str, name: str = "", ends_at: Optional[float] = None, scheduled_for: Optional[datetime] = None) -> dict:
        if self.current is not None:
            raise NetError("A net is already running.")
        config = self._service.saved_config
        now = self._clock()
        limit = now + config.net_max_minutes * 60
        self.current = {
            "id": uuid.uuid4().hex[:12],
            "name": name.strip() or config.net_name,
            "started_at": now,
            "ends_at": min(ends_at, limit) if ends_at else limit,
            "started_by": actor,
            "scheduled_for": scheduled_for.isoformat() if scheduled_for else None,
            "linked_node": None,
            "link_error": None,
            "checkins": [],
        }
        self._save()
        _logger.info("net %r started by %s", self.current["name"], actor)
        self._service.set_net_active(True)
        self._say(config.net_start_say, self.current["name"])
        await self._link_for_net()
        return self.current

    async def end(self, actor: str) -> dict:
        net = self.current
        if net is None:
            raise NetError("No net is running.")
        net = {**net, "ended_at": self._clock(), "ended_by": actor}
        self.current = None
        self.past = [net, *self.past][:MAX_NETS]
        self._save()
        _logger.info("net %r ended by %s", net["name"], actor)
        config = self._service.saved_config
        self._say(config.net_end_say, net["name"])
        self._service.set_net_active(False)
        if net.get("linked_node") and self._links is not None:
            try:
                await self._links.disconnect(net["linked_node"])
            except LinkError as error:
                _logger.warning("couldn't drop the net's link to %s: %s", net["linked_node"], error)
        return net

    def _say(self, template: str, name: str) -> None:
        if template.strip():
            self._service.speak(TTS_PREFIX + template.strip().replace("{name}", name))

    async def _link_for_net(self) -> None:
        config = self._service.saved_config
        if self._links is None or config.net_links == "leave" or self._service.held_reason("links"):
            return
        try:
            if config.net_links == "disconnect":
                await self._links.disconnect_all()
            elif config.net_link_node and config.net_link_node not in await self._links.linked():
                await self._links.connect(config.net_link_node, False)
                self.current["linked_node"] = config.net_link_node
        except LinkError as error:
            _logger.warning("net links: %s", error)
            self.current["link_error"] = str(error)
        self._save()

    def _dtmf(self, action: str, source: str) -> None:
        async def run() -> None:
            try:
                if action == "net_start":
                    await self.start(source)
                    spoken = "" if self._service.saved_config.net_start_say.strip() else "Net mode on."
                else:
                    await self.end(source)
                    spoken = "" if self._service.saved_config.net_end_say.strip() else "Net mode off."
            except NetError:
                spoken = "A net is already running." if action == "net_start" else "No net is running."
            if spoken:
                self._service.speak(TTS_PREFIX + spoken)

        asyncio.get_running_loop().create_task(run())

    async def tick(self) -> None:
        """Ends a net at its time, and starts scheduled ones."""
        if self.current is not None and self._clock() >= self.current["ends_at"]:
            actor = "time limit" if self.current.get("scheduled_for") is None else "schedule"
            net = await self.end(actor)
            self.audit_hook(actor, "Net ended", f"{net['name']}: {len(net['checkins'])} check-ins")
        if self.current is not None:
            return
        now = self._wall_clock()
        for schedule in self._service.saved_config.net_schedules:
            if not schedule.get("enabled", True):
                continue
            start = last_start(schedule, now)
            if start is None:
                continue
            end = window_end(schedule, start)
            if now >= (end or start + SCHEDULE_GRACE) or self._ran(start):
                continue
            ends_at = self._clock() + (end - now).total_seconds() if end else None
            net = await self.start("schedule", ends_at=ends_at, scheduled_for=start)
            self.audit_hook("schedule", "Net started", net["name"])
            return

    def _ran(self, start: datetime) -> bool:
        """Whether the schedule already started a net at `start` (and someone may have ended it)."""
        key = start.isoformat()
        return any(net.get("scheduled_for") == key for net in [*([self.current] if self.current else []), *self.past])

    # -- check-ins --------------------------------------------------------------

    def add_checkin(self, callsign: str, notes: str) -> dict:
        net = self._running()
        if len(net["checkins"]) >= MAX_CHECKINS:
            raise NetError("That's as many check-ins as one net can hold.")
        checkin = {"id": uuid.uuid4().hex[:12], "callsign": callsign.strip().upper(), "at": self._clock(), "notes": notes.strip()}
        net["checkins"].append(checkin)
        self._save()
        return checkin

    def update_checkin(self, checkin_id: str, callsign: str, notes: str) -> dict:
        checkin = self._checkin(checkin_id)
        checkin.update(callsign=callsign.strip().upper(), notes=notes.strip())
        self._save()
        return checkin

    def delete_checkin(self, checkin_id: str) -> None:
        net = self._running()
        checkin = self._checkin(checkin_id)
        net["checkins"].remove(checkin)
        self._save()

    def _running(self) -> dict:
        if self.current is None:
            raise NetError("No net is running.")
        return self.current

    def _checkin(self, checkin_id: str) -> dict:
        for checkin in self._running()["checkins"]:
            if checkin["id"] == checkin_id:
                return checkin
        raise KeyError(checkin_id)

    # -- past nets --------------------------------------------------------------

    def find(self, net_id: str) -> dict:
        for net in [*([self.current] if self.current else []), *self.past]:
            if net["id"] == net_id:
                return net
        raise KeyError(net_id)

    def delete(self, net_id: str) -> None:
        net = self.find(net_id)
        if net is self.current:
            raise NetError("End the net before deleting it.")
        self.past.remove(net)
        self._save()

    def csv(self, net_id: str) -> str:
        net = self.find(net_id)
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(["Net", "Callsign", "Time", "Notes"])
        for checkin in net["checkins"]:
            at = datetime.fromtimestamp(checkin["at"]).strftime("%Y-%m-%d %H:%M:%S")
            writer.writerow([_cell(net["name"]), checkin["callsign"], at, _cell(checkin["notes"])])
        return out.getvalue()
