"""Live audio for the dashboard when the core is a separate process (#229).

In one process the dashboard's listeners subscribe to the audio engine's `AudioMonitor`. With the core
apart, the audio is the core's, so this stands in for the monitor: the same `subscribe` and
`unsubscribe`, fed from the core's `GET /audio` stream. There is one stream per source while anyone
is listening to it and none when nobody is, so an idle dashboard costs the core nothing. If the core
goes away the stream is picked up again when it is back; the listeners just hear nothing meanwhile.
"""
from __future__ import annotations

import asyncio
import json
import logging
import struct
from pathlib import Path
from typing import Optional

import httpx

from .monitor import LISTENER_QUEUE_FRAMES, MonitorSource

_logger = logging.getLogger("moreopenrepeater.remote_audio")

CONNECT_TIMEOUT_SECONDS = 2.0
RETRY_DELAYS = (0.5, 1.0, 2.0, 5.0)  # then the last one, for as long as someone is listening
MAX_FRAME_BYTES = 1024 * 1024  # a frame is about 100 ms of audio; anything near this is not one


class RemoteAudioMonitor:
    def __init__(self, socket_path: Path, device: str) -> None:
        """`device` is `live` (the repeater's audio) or `monitor` (the monitor receiver's)."""
        self._socket_path = Path(socket_path)
        self._device = device
        self._listeners: dict[asyncio.Queue, MonitorSource] = {}
        self._pumps: dict[str, asyncio.Task] = {}
        #: What the core last said its audio is sampled at, once a stream has started.
        self.sample_rate: Optional[int] = None

    @property
    def listener_count(self) -> int:
        return len(self._listeners)

    def subscribe(self, source: MonitorSource) -> asyncio.Queue:
        """Call from the event loop that will read the queue."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=LISTENER_QUEUE_FRAMES)
        self._listeners[queue] = source
        pump = self._pumps.get(source)
        if pump is None or pump.done():
            self._pumps[source] = asyncio.ensure_future(self._pump(source))
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        source = self._listeners.pop(queue, None)
        if source is not None and source not in self._listeners.values():
            pump = self._pumps.pop(source, None)
            if pump is not None:
                pump.cancel()

    def close(self) -> None:
        """Stop reading from the core. Listeners that are left hear nothing more."""
        self._listeners.clear()
        for pump in self._pumps.values():
            pump.cancel()
        self._pumps.clear()

    # -- the stream from the core ------------------------------------------------------------

    async def _pump(self, source: str) -> None:
        attempt = 0
        while source in self._listeners.values():
            try:
                await self._read(source)
                attempt = 0  # it connected, streamed, and ended: start the backoff over
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001 - a bad stream must never end the loop
                if attempt == 0:
                    _logger.warning("the core's %s audio stream ended: %s", self._device, type(error).__name__)
            await asyncio.sleep(RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)])
            attempt += 1

    async def _read(self, source: str) -> None:
        transport = httpx.AsyncHTTPTransport(uds=str(self._socket_path))
        # No read timeout: a core with its audio off sends nothing, and that is not an error.
        timeout = httpx.Timeout(CONNECT_TIMEOUT_SECONDS, read=None)
        async with httpx.AsyncClient(transport=transport, base_url="http://core", timeout=timeout) as client:
            async with client.stream("GET", "/audio", params={"device": self._device, "source": source}) as response:
                if response.status_code != 200:
                    raise RuntimeError(f"core answered {response.status_code}")
                buffer = bytearray()
                header_seen = False
                async for chunk in response.aiter_raw():
                    buffer += chunk
                    while len(buffer) >= 4:
                        (size,) = struct.unpack(">I", buffer[:4])
                        if size > MAX_FRAME_BYTES:
                            raise RuntimeError("audio frame too large")
                        if len(buffer) < 4 + size:
                            break
                        payload = bytes(buffer[4 : 4 + size])
                        del buffer[: 4 + size]
                        if not header_seen:
                            header_seen = True
                            rate = json.loads(payload).get("sample_rate")
                            if isinstance(rate, int) and rate > 0:
                                self.sample_rate = rate
                            continue
                        self._dispatch(source, payload)

    def _dispatch(self, source: str, frame: bytes) -> None:
        for queue, wanted in list(self._listeners.items()):
            if wanted != source:
                continue
            try:
                queue.put_nowait(frame)
            except asyncio.QueueFull:
                pass  # a listener that falls behind loses audio rather than building up delay
