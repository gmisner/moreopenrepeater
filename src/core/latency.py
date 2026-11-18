"""Opt-in latency diagnostics for the core (#229).

An "audio glitch" in the core's log says the sound card ran dry or overflowed. It does not say why. In
one process that was the dashboard's own work, and now it can be the garbage collector or a handler
that held the interpreter lock. With `MOREOPENREPEATER_DIAG_LATENCY=1` this watches for the three
things that can hold the audio thread up from inside the process, and writes each one to the core's own
log, in the same file and clock as the glitch lines, so they can be matched:

- a garbage collection that took longer than `gc_ms`, with its generation;
- a thread that asked to sleep 5 ms and woke `stall_ms` or more late (the interpreter lock, a
  collection, or the CPU, as seen by an ordinary Python thread);
- an event loop whose timers fire `lag_ms` or more late (a handler that blocked it).

The sound card calls back into Python every 20 ms (one block), and the callback needs the interpreter
lock to run, so a hold much longer than that is heard as an overrun. The defaults are set just under a
block so that anything that could cause one is seen.

It reads clocks and writes a log line only when a threshold is crossed, so it costs next to nothing and
never touches the audio path. It is off by default.
"""
from __future__ import annotations

import asyncio
import gc
import logging
import threading
import time
from typing import Mapping, Optional

_logger = logging.getLogger("moreopenrepeater.latency")

ENV = "MOREOPENREPEATER_DIAG_LATENCY"
TICK_SECONDS = 0.005  # the watcher thread asks to sleep this long
LOOP_TICK_SECONDS = 0.05  # and the event loop is asked to call back this often


def enabled_from_env(env: Mapping[str, str]) -> bool:
    return (env.get(ENV) or "").strip() == "1"


class LatencyWatch:
    def __init__(self, gc_ms: float = 10.0, stall_ms: float = 15.0, lag_ms: float = 15.0) -> None:
        self._gc_s, self._stall_s, self._lag_s = gc_ms / 1000, stall_ms / 1000, lag_ms / 1000
        self._gc_started: Optional[float] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_handle: Optional[asyncio.TimerHandle] = None
        self._loop_due = 0.0

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, loop: Optional[asyncio.AbstractEventLoop] = None) -> None:
        """Begin watching. Pass the event loop to watch it too; call from a thread that may touch it."""
        if self.running:
            return
        self._stop.clear()
        gc.callbacks.append(self._on_gc)
        self._thread = threading.Thread(target=self._watch_thread, name="latency-watch", daemon=True)
        self._thread.start()
        if loop is not None:
            self._loop = loop
            self._loop_due = time.perf_counter() + LOOP_TICK_SECONDS
            self._loop_handle = loop.call_later(LOOP_TICK_SECONDS, self._on_loop_tick)
        _logger.info(
            "latency diagnostics on: collections over %.0f ms, held threads over %.0f ms, loop lag over %.0f ms are logged",
            self._gc_s * 1000, self._stall_s * 1000, self._lag_s * 1000,
        )

    def stop(self) -> None:
        self._stop.set()
        try:
            gc.callbacks.remove(self._on_gc)
        except ValueError:
            pass
        if self._loop_handle is not None:
            self._loop_handle.cancel()
            self._loop_handle = None
        if self._thread is not None:
            self._thread.join(2)
            self._thread = None

    # -- the three watchers ------------------------------------------------------------------

    def _on_gc(self, phase: str, info: dict) -> None:
        if phase == "start":
            self._gc_started = time.perf_counter()
            return
        started, self._gc_started = self._gc_started, None
        if started is None:
            return
        took = time.perf_counter() - started
        if took >= self._gc_s:
            _logger.info(
                "garbage collection (generation %d) took %.0f ms, %d objects collected",
                info.get("generation", -1), took * 1000, info.get("collected", 0),
            )

    def _watch_thread(self) -> None:
        while not self._stop.is_set():
            before = time.perf_counter()
            time.sleep(TICK_SECONDS)
            late = time.perf_counter() - before - TICK_SECONDS
            if late >= self._stall_s:
                _logger.info("a thread was held for %.0f ms (the interpreter lock, a collection, or the CPU)", late * 1000)

    def _on_loop_tick(self) -> None:
        late = time.perf_counter() - self._loop_due
        if late >= self._lag_s:
            _logger.info("the event loop was %.0f ms late (a handler held it)", late * 1000)
        if self._loop is not None and not self._stop.is_set():
            self._loop_due = time.perf_counter() + LOOP_TICK_SECONDS
            self._loop_handle = self._loop.call_later(LOOP_TICK_SECONDS, self._on_loop_tick)
