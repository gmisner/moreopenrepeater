"""Net mode: one switch that sets the repeater up for a net, and the net's
check-in log.

While a net runs, `controller.modes` applies the net settings (timeout,
hang time, courtesy tone, the phone patch held) over the saved ones, and
scheduled announcements wait until it's over. Starting it can also drop all
links or link a net's hub, and ending it drops the link it made. A net starts
from the dashboard, a DTMF macro, a weekly schedule, or a one-shot plan, and
ends from any of those or after `net_max_minutes`, so it can't be left on.

Net control logs check-ins (callsign, time, name, location, license class,
staying-for-comments, notes); the running net and the last MAX_NETS are kept
in a JSON file, and each exports as CSV or PDF.
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
from .net_pdf import net_pdf
from .persistence import StateStore
from .service.core_handle import ApiCoreHandle
from .weekly import last_start, next_start, starts_between, window_end

_logger = logging.getLogger("moreopenrepeater.net")

MAX_NETS = 50
MAX_CHECKINS = 500
MAX_PLANNED = 100
POLL_SECONDS = 15
SCHEDULE_GRACE = timedelta(minutes=5)  # a schedule without an end only starts the net this soon after its time


class NetError(Exception):
    pass


def _cell(text: str) -> str:
    """A leading = + - @ would make a spreadsheet run the cell as a formula."""
    return "'" + text if text[:1] in ("=", "+", "-", "@") else text


def _summary(net: dict) -> dict:
    return {k: v for k, v in net.items() if k != "checkins"} | {"checkin_count": len(net.get("checkins", []))}


def _person(callsign: str, name: str) -> str:
    return " ".join(part for part in (callsign, name) if part).strip()


class NetMode:
    """`store=None` keeps nets in memory (tests)."""

    def __init__(
        self,
        service: ApiCoreHandle,
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
        self.planned: list[dict] = data.get("planned", [])
        service.register_macro_handler("net", self._dtmf)
        if self.current is not None:  # a net that was running when the service stopped
            service.sync_net_state(True)

    def _save(self) -> None:
        if self._store is not None:
            self._store.save({"current": self.current, "past": self.past, "planned": self.planned})

    def status(self) -> dict:
        now = self._wall_clock()
        starts = [s for s in (next_start(schedule, now) for schedule in self._service.saved_config.net_schedules) if s]
        planned_starts = [
            datetime.fromisoformat(item["starts_at"])
            for item in self.planned
            if datetime.fromisoformat(item["starts_at"]) > now
        ]
        nexts = [*starts, *planned_starts]
        return {
            "current": self.current,
            "past": [_summary(net) for net in self.past],
            "planned": list(self.planned),
            "next_scheduled": min(nexts) if nexts else None,
        }

    # -- starting and ending --------------------------------------------------

    async def start(
        self,
        actor: str,
        name: str = "",
        ends_at: Optional[float] = None,
        scheduled_for: Optional[datetime] = None,
        *,
        control_callsign: str = "",
        control_name: str = "",
        logger_callsign: str = "",
        logger_name: str = "",
        club_name: Optional[str] = None,
        frequency: Optional[str] = None,
    ) -> dict:
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
            "club_name": (config.net_club_name if club_name is None else club_name).strip(),
            "frequency": (config.net_frequency if frequency is None else frequency).strip(),
            "control_callsign": (control_callsign.strip() or config.net_control_callsign or "").strip().upper(),
            "control_name": (control_name.strip() or config.net_control_name or "").strip(),
            "logger_callsign": (logger_callsign.strip() or config.net_logger_callsign or "").strip().upper(),
            "logger_name": (logger_name.strip() or config.net_logger_name or "").strip(),
            "checkins": [],
        }
        self._save()
        _logger.info("net %r started by %s", self.current["name"], actor)
        await self._service.command("set_net_active", active=True)
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
        await self._service.command("set_net_active", active=False)
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
        """Ends a net at its time, and starts scheduled or planned ones."""
        if self.current is not None and self._clock() >= self.current["ends_at"]:
            actor = "time limit" if self.current.get("scheduled_for") is None else "schedule"
            net = await self.end(actor)
            self.audit_hook(actor, "Net ended", f"{net['name']}: {len(net['checkins'])} check-ins")
        if self.current is not None:
            return
        now = self._wall_clock()
        if await self._start_due_plan(now):
            return
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
            net = await self.start(
                "schedule",
                name=schedule.get("name") or "",
                ends_at=ends_at,
                scheduled_for=start,
                control_callsign=schedule.get("control_callsign") or "",
                control_name=schedule.get("control_name") or "",
                logger_callsign=schedule.get("logger_callsign") or "",
                logger_name=schedule.get("logger_name") or "",
            )
            self.audit_hook("schedule", "Net started", net["name"])
            return

    async def _start_due_plan(self, now: datetime) -> bool:
        keep: list[dict] = []
        due: list[dict] = []
        for item in self.planned:
            start = datetime.fromisoformat(item["starts_at"])
            if start + SCHEDULE_GRACE <= now:
                continue  # missed or already handled
            if start <= now and not self._ran(start):
                due.append(item)
            else:
                keep.append(item)
        if len(keep) + len(due) != len(self.planned):
            self.planned = keep + due
            self._save()
        if not due or self.current is not None:
            return False
        plan = sorted(due, key=lambda item: item["starts_at"])[0]
        minutes = int(plan.get("minutes") or 0)
        ends_at = self._clock() + minutes * 60 if minutes else None
        start = datetime.fromisoformat(plan["starts_at"])
        net = await self.start(
            "plan",
            name=plan.get("name") or "",
            ends_at=ends_at,
            scheduled_for=start,
            control_callsign=plan.get("control_callsign") or "",
            control_name=plan.get("control_name") or "",
            logger_callsign=plan.get("logger_callsign") or "",
            logger_name=plan.get("logger_name") or "",
        )
        self.planned = [item for item in self.planned if item["id"] != plan["id"]]
        self._save()
        self.audit_hook("plan", "Net started", net["name"])
        return True

    def _ran(self, start: datetime) -> bool:
        """Whether the schedule already started a net at `start` (and someone may have ended it)."""
        key = start.isoformat()
        return any(net.get("scheduled_for") == key for net in [*([self.current] if self.current else []), *self.past])

    # -- one-shot plans ---------------------------------------------------------

    def plan(
        self,
        *,
        name: str,
        starts_at: datetime,
        minutes: int = 60,
        control_callsign: str = "",
        control_name: str = "",
        logger_callsign: str = "",
        logger_name: str = "",
    ) -> dict:
        if len(self.planned) >= MAX_PLANNED:
            raise NetError("That's as many planned nets as we keep.")
        if starts_at <= self._wall_clock():
            raise NetError("Pick a future start time.")
        config = self._service.saved_config
        item = {
            "id": uuid.uuid4().hex[:12],
            "name": name.strip() or config.net_name,
            "starts_at": starts_at.replace(second=0, microsecond=0).isoformat(),
            "minutes": max(0, int(minutes)),
            "control_callsign": (control_callsign.strip() or config.net_control_callsign or "").strip().upper(),
            "control_name": (control_name.strip() or config.net_control_name or "").strip(),
            "logger_callsign": (logger_callsign.strip() or config.net_logger_callsign or "").strip().upper(),
            "logger_name": (logger_name.strip() or config.net_logger_name or "").strip(),
        }
        self.planned = sorted([*self.planned, item], key=lambda row: row["starts_at"])
        self._save()
        return item

    def cancel_plan(self, plan_id: str) -> None:
        before = len(self.planned)
        self.planned = [item for item in self.planned if item["id"] != plan_id]
        if len(self.planned) == before:
            raise KeyError(plan_id)
        self._save()

    # -- check-ins --------------------------------------------------------------

    def add_checkin(
        self,
        callsign: str,
        notes: str = "",
        *,
        name: str = "",
        location: str = "",
        license_class: str = "",
        staying_for_comments: bool = False,
    ) -> dict:
        net = self._running()
        if len(net["checkins"]) >= MAX_CHECKINS:
            raise NetError("That's as many check-ins as one net can hold.")
        checkin = {
            "id": uuid.uuid4().hex[:12],
            "callsign": callsign.strip().upper(),
            "at": self._clock(),
            "notes": notes.strip(),
            "name": name.strip(),
            "location": location.strip(),
            "license_class": license_class.strip(),
            "staying_for_comments": bool(staying_for_comments),
        }
        net["checkins"].append(checkin)
        self._save()
        return checkin

    def update_checkin(
        self,
        checkin_id: str,
        callsign: str,
        notes: str = "",
        *,
        name: str = "",
        location: str = "",
        license_class: str = "",
        staying_for_comments: bool = False,
        net_id: Optional[str] = None,
    ) -> dict:
        checkin = self._checkin(checkin_id, net_id=net_id)
        checkin.update(
            callsign=callsign.strip().upper(),
            notes=notes.strip(),
            name=name.strip(),
            location=location.strip(),
            license_class=license_class.strip(),
            staying_for_comments=bool(staying_for_comments),
        )
        self._save()
        return checkin

    def delete_checkin(self, checkin_id: str, *, net_id: Optional[str] = None) -> None:
        net = self.find(net_id) if net_id else self._running()
        checkin = self._checkin(checkin_id, net_id=net_id)
        net["checkins"].remove(checkin)
        self._save()

    def _running(self) -> dict:
        if self.current is None:
            raise NetError("No net is running.")
        return self.current

    def _checkin(self, checkin_id: str, *, net_id: Optional[str] = None) -> dict:
        net = self.find(net_id) if net_id else self._running()
        for checkin in net["checkins"]:
            if checkin["id"] == checkin_id:
                return checkin
        raise KeyError(checkin_id)

    # -- past / detail / calendar / reports -------------------------------------

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

    def calendar(self, begin: datetime, end: datetime) -> list[dict]:
        events: list[dict] = []
        for net in self.past:
            started = datetime.fromtimestamp(net["started_at"])
            if begin <= started < end:
                events.append(
                    {
                        "kind": "past",
                        "id": net["id"],
                        "name": net["name"],
                        "starts_at": started.isoformat(),
                        "checkin_count": len(net.get("checkins", [])),
                    }
                )
        if self.current is not None:
            started = datetime.fromtimestamp(self.current["started_at"])
            if begin <= started < end:
                events.append(
                    {
                        "kind": "current",
                        "id": self.current["id"],
                        "name": self.current["name"],
                        "starts_at": started.isoformat(),
                        "checkin_count": len(self.current.get("checkins", [])),
                    }
                )
        for plan in self.planned:
            starts = datetime.fromisoformat(plan["starts_at"])
            if begin <= starts < end:
                events.append(
                    {
                        "kind": "planned",
                        "id": plan["id"],
                        "name": plan["name"],
                        "starts_at": plan["starts_at"],
                        "minutes": plan.get("minutes", 60),
                    }
                )
        for index, schedule in enumerate(self._service.saved_config.net_schedules):
            for start in starts_between(schedule, begin, end):
                if self._ran(start):
                    continue
                events.append(
                    {
                        "kind": "schedule",
                        "id": f"schedule-{index}-{start.isoformat()}",
                        "name": schedule.get("name") or self._service.saved_config.net_name,
                        "starts_at": start.isoformat(),
                        "minutes": schedule.get("minutes", 60),
                        "every": schedule.get("every") or "weekly",
                    }
                )
        return sorted(events, key=lambda row: row["starts_at"])

    def attendance(self, name: str = "") -> list[dict]:
        """Check-in counts per callsign across past (+ current) nets with the same name."""
        needle = name.strip() or (self.current["name"] if self.current else "")
        if not needle:
            return []
        counts: dict[str, int] = {}
        for net in [*([self.current] if self.current else []), *self.past]:
            if net.get("name") != needle:
                continue
            for checkin in net.get("checkins", []):
                call = checkin.get("callsign", "")
                if call:
                    counts[call] = counts.get(call, 0) + 1
        return [{"callsign": call, "count": counts[call]} for call in sorted(counts, key=lambda c: (-counts[c], c))]

    def summary(self, begin: float, end: float) -> dict:
        nets = []
        for net in self.past:
            if begin <= net["started_at"] < end:
                nets.append(net)
        if self.current is not None and begin <= self.current["started_at"] < end:
            nets.append(self.current)
        checkin_total = sum(len(net.get("checkins", [])) for net in nets)
        by_name: dict[str, dict] = {}
        for net in nets:
            bucket = by_name.setdefault(net["name"], {"name": net["name"], "nets": 0, "checkins": 0})
            bucket["nets"] += 1
            bucket["checkins"] += len(net.get("checkins", []))
        return {
            "from": begin,
            "to": end,
            "net_count": len(nets),
            "checkin_count": checkin_total,
            "by_name": sorted(by_name.values(), key=lambda row: row["name"]),
        }

    def csv(self, net_id: str) -> str:
        net = self.find(net_id)
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(
            [
                "Net",
                "Club",
                "Frequency",
                "Net control",
                "Logger",
                "Callsign",
                "Time",
                "Name",
                "Location",
                "License class",
                "Staying for comments",
                "Notes",
            ]
        )
        control = _person(net.get("control_callsign", ""), net.get("control_name", ""))
        logger = _person(net.get("logger_callsign", ""), net.get("logger_name", ""))
        for checkin in net["checkins"]:
            at = datetime.fromtimestamp(checkin["at"]).strftime("%Y-%m-%d %H:%M:%S")
            writer.writerow(
                [
                    _cell(net["name"]),
                    _cell(net.get("club_name", "")),
                    _cell(net.get("frequency", "")),
                    _cell(control),
                    _cell(logger),
                    checkin["callsign"],
                    at,
                    _cell(checkin.get("name", "")),
                    _cell(checkin.get("location", "")),
                    _cell(checkin.get("license_class", "")),
                    "yes" if checkin.get("staying_for_comments") else "no",
                    _cell(checkin.get("notes", "")),
                ]
            )
        return out.getvalue()

    def pdf(self, net_id: str) -> bytes:
        return net_pdf(self.find(net_id))
