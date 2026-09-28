"""Fans live audio out to dashboard listeners.

The audio worker thread calls `feed()` with every processing block; blocks
are batched into ~100 ms frames of 16-bit PCM and handed to each listener's
queue on the event loop. A listener that falls behind loses frames rather
than building up latency (or memory).
"""
from __future__ import annotations

import asyncio
from typing import Literal, Optional

import numpy as np

MonitorSource = Literal["rx", "tx"]  # what the receiver hears / what's transmitted
FRAME_BLOCKS = 5
LISTENER_QUEUE_FRAMES = 20


def pcm16(samples: np.ndarray) -> bytes:
    return (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2").tobytes()


class AudioMonitor:
    def __init__(self) -> None:
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._listeners: dict[asyncio.Queue, MonitorSource] = {}
        self._rx: list[np.ndarray] = []
        self._tx: list[np.ndarray] = []

    @property
    def listener_count(self) -> int:
        return len(self._listeners)

    def subscribe(self, source: MonitorSource) -> asyncio.Queue:
        """Call from the event loop that will read the queue."""
        self._loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue(maxsize=LISTENER_QUEUE_FRAMES)
        self._listeners[queue] = source
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._listeners.pop(queue, None)

    def feed(self, received: np.ndarray, transmitted: np.ndarray) -> None:
        """Worker thread."""
        if not self._listeners or self._loop is None:
            self._rx.clear()
            self._tx.clear()
            return
        self._rx.append(received)
        self._tx.append(transmitted)
        if len(self._rx) >= FRAME_BLOCKS:
            frames = {"rx": pcm16(np.concatenate(self._rx)), "tx": pcm16(np.concatenate(self._tx))}
            self._rx.clear()
            self._tx.clear()
            self._loop.call_soon_threadsafe(self._dispatch, frames)

    def _dispatch(self, frames: dict[str, bytes]) -> None:
        for queue, source in list(self._listeners.items()):
            try:
                queue.put_nowait(frames[source])
            except asyncio.QueueFull:
                pass
