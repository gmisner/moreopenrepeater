"""AllStar audio: the repeater as its AllStarLink node's radio, over USRP.

The node's `rxchannel` is `USRP/<our host>:<our port>:<node port>` (see
link.usrp). What the controller repeats goes to the node, which sends it
over its links; what the node transmits -- its links' audio and its own
announcements -- comes back and is transmitted here, keying the repeater
the way a linked node keying up does.

The node needs `duplex = 0` in rpt.conf: as a repeater (`duplex = 2`) it
would send everything we give it straight back, and it'd go out twice.
"""
from __future__ import annotations

import asyncio
import logging
import socket
from typing import NamedTuple, Optional

import numpy as np

from audio_io.patch import LinkAudio
from audio_io.resample import StreamResampler
from controller.events import RemoteKeyed
from link.usrp import FRAME_SAMPLES, RATE, decode, encode_voice

from .service import LINK_AUDIO_NODE, RepeaterService

_logger = logging.getLogger("moreopenrepeater.allstar_audio")

FRAME_SECONDS = FRAME_SAMPLES / RATE
UNKEY_TIMEOUT = 0.5  # keyed with no packets this long: the unkey packet was lost


class UsrpSettings(NamedTuple):
    listen_host: str
    listen_port: int
    node_host: str
    node_port: int


def _host_port(value: str) -> tuple[str, int]:
    host, _, port = value.rpartition(":")
    return host or "127.0.0.1", int(port)


def usrp_listen_from_env(env: dict) -> tuple[str, int]:
    return _host_port(env.get("MOREOPENREPEATER_USRP_LISTEN", "127.0.0.1:34001"))


def usrp_settings_from_env(env: dict) -> Optional[UsrpSettings]:
    """A node set up by hand (docs/allstar.md), rather than from the dashboard."""
    node = env.get("MOREOPENREPEATER_USRP_NODE")
    if not node:
        return None
    listen_host, listen_port = usrp_listen_from_env(env)
    node_host, node_port = _host_port(node)
    return UsrpSettings(listen_host, listen_port, node_host, node_port)


def _to_pcm(samples: np.ndarray) -> bytes:
    return (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2").tobytes()


def _from_pcm(payload: bytes) -> np.ndarray:
    return np.frombuffer(payload, dtype="<i2").astype(np.float32) / 32768


class AllStarAudio(asyncio.DatagramProtocol):
    def __init__(self, service: RepeaterService, settings: Optional[UsrpSettings]) -> None:
        self._service = service
        self.settings = settings
        self.error: Optional[str] = None
        self.keyed = False
        self.listen_port = settings.listen_port if settings else 0
        self._transport: Optional[asyncio.DatagramTransport] = None
        self._task: Optional[asyncio.Task] = None
        self._link: Optional[LinkAudio] = None
        self._node_ip: Optional[str] = None
        self._to_node: Optional[StreamResampler] = None
        self._from_node: Optional[StreamResampler] = None
        self._seq = 0
        self._last_packet = 0.0

    @property
    def rate(self) -> int:
        return self._service.renderer.sample_rate if self._service.renderer else 16000

    def status(self) -> dict:
        s = self.settings
        return {
            "configured": s is not None,
            "running": self._task is not None and not self._task.done(),
            "error": self.error,
            "node": f"{s.node_host}:{s.node_port}" if s else None,
            "keyed": self.keyed,
        }

    async def start(self) -> None:
        s = self.settings
        if s is None:
            return
        loop = asyncio.get_running_loop()
        try:
            self._node_ip = socket.gethostbyname(s.node_host)
            self._transport, _ = await loop.create_datagram_endpoint(lambda: self, local_addr=(s.listen_host, s.listen_port))
        except OSError as error:
            self.error = f"couldn't set up USRP on {s.listen_host}:{s.listen_port} for the node at {s.node_host}: {error}"
            _logger.error("%s", self.error)
            return
        self.listen_port = self._transport.get_extra_info("sockname")[1]
        self._link = LinkAudio(self.rate)
        self._to_node = StreamResampler(self.rate, RATE)
        self._from_node = StreamResampler(RATE, self.rate)
        self._set_output(self._link)
        self._task = asyncio.create_task(self._send())
        self._service.link_audio = True
        _logger.info("AllStar audio over USRP: listening on %s:%s, node at %s:%s", s.listen_host, s.listen_port, s.node_host, s.node_port)

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        if self._link is not None:
            self._set_output(None)
            self._link = None
        if self._transport is not None:
            self._transport.close()
            self._transport = None
        if self.keyed:
            self._set_keyed(False)
        self._service.link_audio = False

    async def configure(self, settings: Optional[UsrpSettings]) -> None:
        """Switch to another node (or none), e.g. after the dashboard sets one up."""
        if settings == self.settings and (settings is None or self._task is not None):
            return
        await self.stop()
        self.settings = settings
        self.error = None
        await self.start()

    # -- to the node ---------------------------------------------------------

    async def _send(self) -> None:
        """Every 20 ms, what's been repeated since, and a check for a lost unkey."""
        assert self._link is not None and self._to_node is not None and self._transport is not None
        s = self.settings
        assert s is not None
        loop = asyncio.get_running_loop()
        pending = np.zeros(0, dtype=np.float32)
        next_at = loop.time()
        while True:
            while (block := self._link.take_radio()) is not None:
                pending = np.concatenate([pending, self._to_node.process(block)])
            while len(pending) >= FRAME_SAMPLES:
                self._seq += 1
                self._transport.sendto(encode_voice(self._seq, _to_pcm(pending[:FRAME_SAMPLES])), (s.node_host, s.node_port))
                pending = pending[FRAME_SAMPLES:]
            if self.keyed and loop.time() - self._last_packet > UNKEY_TIMEOUT:
                _logger.warning("no unkey from the node; unkeying")
                self._set_keyed(False)
            next_at += FRAME_SECONDS
            await asyncio.sleep(max(0.0, next_at - loop.time()))

    # -- from the node -------------------------------------------------------

    def datagram_received(self, data: bytes, addr: tuple) -> None:
        if addr[0] != self._node_ip or self._link is None or self._from_node is None:
            return
        packet = decode(data)
        if packet is None:
            return
        if packet.audio:
            self._last_packet = asyncio.get_running_loop().time()
            self._link.add_phone(self._from_node.process(_from_pcm(packet.audio)))
            if packet.keyup and not self.keyed:
                self._set_keyed(True)
        elif not packet.keyup and self.keyed:
            self._set_keyed(False)

    def error_received(self, exc: Exception) -> None:
        # The node not listening yet (Asterisk restarting) shows up as ICMP errors.
        _logger.debug("USRP: %r", exc)

    def _set_keyed(self, keyed: bool) -> None:
        self.keyed = keyed
        self._service.handle_link_event(RemoteKeyed(node_id=LINK_AUDIO_NODE, keyed=keyed))

    def _set_output(self, link: Optional[LinkAudio]) -> None:
        output = self._service.audio_output
        if output is not None:
            output.set_link(link)
