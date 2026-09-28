"""Bridges a RepeaterController to the API/dashboard layer.

Runs the controller in-process on the same asyncio event loop as FastAPI --
all mutation happens synchronously within request handlers or the periodic
tick loop, so no locking is needed. Exposes simulate_* methods that inject
synthetic events (COS, CTCSS, DTMF, remote-node keying) so the dashboard is
usable for development/demo without real audio_io/link hardware wired up
yet; those are also the seam audio_io/link will eventually feed instead.
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging
import time
from typing import Callable, Optional

from controller.events import (
    AssertPTT,
    COSChanged,
    CTCSSChanged,
    ControllerCommand,
    ControllerEvent,
    DTMFDigit,
    LinkStateChanged,
    PlayAudio,
    RemoteKeyed,
    SendLinkCommand,
)
from controller.macros import Macro
from controller.state_machine import RepeaterConfig, RepeaterController

_logger = logging.getLogger("moreopenrepeater.service")


@dataclasses.dataclass(frozen=True)
class StatusSnapshot:
    state: str
    ptt_active: bool
    cos_active: bool
    ctcss_hz: Optional[float]
    linked_nodes: list[str]
    last_clip: Optional[str]
    timestamp: float


class RepeaterService:
    def __init__(
        self,
        config: Optional[RepeaterConfig] = None,
        macros: Optional[list[Macro]] = None,
        clock: Callable[[], float] = time.monotonic,
        link_command_sink: Callable[[SendLinkCommand], None] = lambda command: None,
    ) -> None:
        self._clock = clock
        self._link_command_sink = link_command_sink
        self.controller = RepeaterController(config or RepeaterConfig(), macros=macros or [], now=clock())
        self.ptt_active = False
        self.cos_active = False
        self.ctcss_hz: Optional[float] = None
        self.linked_nodes: set[str] = set()
        self.last_clip: Optional[str] = None
        self._subscribers: set["asyncio.Queue[StatusSnapshot]"] = set()

    @property
    def config(self) -> RepeaterConfig:
        return self.controller.config

    def set_link_command_sink(self, sink: Callable[[SendLinkCommand], None]) -> None:
        self._link_command_sink = sink

    def snapshot(self) -> StatusSnapshot:
        return StatusSnapshot(
            state=self.controller.state,
            ptt_active=self.ptt_active,
            cos_active=self.cos_active,
            ctcss_hz=self.ctcss_hz,
            linked_nodes=sorted(self.linked_nodes),
            last_clip=self.last_clip,
            timestamp=self._clock(),
        )

    def subscribe(self) -> "asyncio.Queue[StatusSnapshot]":
        queue: "asyncio.Queue[StatusSnapshot]" = asyncio.Queue()
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: "asyncio.Queue[StatusSnapshot]") -> None:
        self._subscribers.discard(queue)

    def _notify(self) -> None:
        snapshot = self.snapshot()
        for queue in self._subscribers:
            queue.put_nowait(snapshot)

    def _apply_commands(self, commands: list[ControllerCommand]) -> None:
        for command in commands:
            if isinstance(command, AssertPTT):
                self.ptt_active = command.active
            elif isinstance(command, PlayAudio):
                self.last_clip = command.clip
            elif isinstance(command, SendLinkCommand):
                self._link_command_sink(command)
        self._notify()

    def tick(self) -> None:
        self._apply_commands(self.controller.tick(self._clock()))

    def simulate_cos(self, active: bool) -> None:
        _logger.info("simulate_cos(active=%s)", active)
        self.cos_active = active
        self._apply_commands(self.controller.handle_event(COSChanged(active=active), self._clock()))

    def simulate_ctcss(self, tone_hz: Optional[float]) -> None:
        _logger.info("simulate_ctcss(tone_hz=%s)", tone_hz)
        self.ctcss_hz = tone_hz
        self._apply_commands(self.controller.handle_event(CTCSSChanged(tone_hz=tone_hz), self._clock()))

    def simulate_dtmf(self, digit: str) -> None:
        _logger.info("simulate_dtmf(digit=%r)", digit)
        self._apply_commands(self.controller.handle_event(DTMFDigit(digit=digit), self._clock()))

    def simulate_remote_keyed(self, node_id: str, keyed: bool) -> None:
        _logger.info("simulate_remote_keyed(node_id=%r, keyed=%s)", node_id, keyed)
        if keyed:
            self.linked_nodes.add(node_id)
        else:
            self.linked_nodes.discard(node_id)
        self._apply_commands(self.controller.handle_event(RemoteKeyed(node_id=node_id, keyed=keyed), self._clock()))

    def handle_link_event(self, event: ControllerEvent) -> None:
        """Apply a real event from `link.node_link.NodeLinkClient` -- the
        same shape as simulate_remote_keyed's bookkeeping, but driven by the
        network layer instead of a dashboard button."""
        _logger.info("handle_link_event(%r)", event)
        if isinstance(event, LinkStateChanged):
            if event.linked:
                self.linked_nodes.add(event.node_id)
            else:
                self.linked_nodes.discard(event.node_id)
        self._apply_commands(self.controller.handle_event(event, self._clock()))

    def update_config(self, **overrides: object) -> RepeaterConfig:
        _logger.info("update_config(%s)", overrides)
        new_config = dataclasses.replace(self.controller.config, **overrides)
        self.controller.config = new_config
        self._notify()
        return new_config

    def list_macros(self) -> list[Macro]:
        return self.controller.list_macros()

    def add_macro(self, macro: Macro) -> list[Macro]:
        _logger.info("add_macro(pattern=%r, command=%r)", macro.pattern, macro.command)
        macros = [m for m in self.controller.list_macros() if m.pattern != macro.pattern]
        macros.append(macro)
        self.controller.set_macros(macros)
        return self.controller.list_macros()

    def delete_macro(self, pattern: str) -> list[Macro]:
        _logger.info("delete_macro(pattern=%r)", pattern)
        macros = [m for m in self.controller.list_macros() if m.pattern != pattern]
        self.controller.set_macros(macros)
        return macros

    def export_snapshot(self) -> dict:
        return {
            "config": dataclasses.asdict(self.controller.config),
            "macros": [dataclasses.asdict(m) for m in self.controller.list_macros()],
        }

    def import_snapshot(self, data: dict) -> None:
        self.update_config(**data["config"])
        self.controller.set_macros([Macro(**m) for m in data["macros"]])
