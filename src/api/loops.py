"""Background loops from the app runtime that carry timers, IDs and announcements.

Each survives an unexpected exception in one pass (see `supervise.LoopGuard`).
`asyncio.CancelledError` is not an `Exception`, so shutdown still stops them.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable, MutableSequence

from .supervise import LoopGuard

_tick_logger = logging.getLogger("moreopenrepeater.tick")


async def run_tick_loop(service, last_tick: MutableSequence[float], interval: float) -> None:
    """Drive the controller every `interval` seconds.

    A pass that raises is logged and skipped, and the loop keeps going. It does
    not update `last_tick` on a failed pass: the watchdog watches `last_tick`, so
    a tick that fails for long enough still ends in a PTT release and a restart,
    instead of looking healthy while no timer runs.
    """
    guard = LoopGuard("controller tick", _tick_logger)
    last_tick[0] = time.monotonic()
    while True:
        await asyncio.sleep(interval)
        try:
            service.tick()
        except Exception as error:  # noqa: BLE001 - see the docstring
            guard.failed(error)
        else:
            guard.ok()
            last_tick[0] = time.monotonic()


async def run_announcement_loop(
    service,
    render_and_queue: Callable[..., Awaitable[None]],
    poll_seconds: float,
    expected_errors: tuple[type[BaseException], ...],
    logger: logging.Logger,
) -> None:
    """Play scheduled announcements when they fall due.

    `expected_errors` are per-announcement problems (an unknown clip, a TTS
    failure) that skip just that announcement. Anything else skips the pass.
    """
    guard = LoopGuard("announcement scheduler", logger)
    while True:
        await asyncio.sleep(poll_seconds)
        try:
            for clip, on_linked_radio in service.due_announcements():
                try:
                    await render_and_queue(clip, on_linked_radio=on_linked_radio)
                except expected_errors:
                    logger.exception("couldn't play scheduled announcement %s", clip)
        except Exception as error:  # noqa: BLE001
            guard.failed(error)
        else:
            guard.ok()


async def run_long_id_warm_loop(
    warm: Callable[[object], None], current_config: Callable[[], object], poll_seconds: float, logger: logging.Logger
) -> None:
    """Re-render the long ID in the background so it is ready when due."""
    guard = LoopGuard("long ID warm-up", logger)
    loop = asyncio.get_running_loop()
    while True:
        await asyncio.sleep(poll_seconds)
        try:
            await loop.run_in_executor(None, warm, current_config())
        except Exception as error:  # noqa: BLE001
            guard.failed(error)
        else:
            guard.ok()
