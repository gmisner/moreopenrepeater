"""Looking up the AllStar node's or the DMR bridge's address must not stop the core's event loop (review finding).

A stalled resolver is a `socket.getaddrinfo` that sleeps. `socket.gethostbyname` on the loop's thread would hold the
whole loop for that long; the lookup now runs in a thread. Simulation: it shows the loop keeps running, not how a real
resolver on a real site behaves.
"""
import asyncio
import socket
import time

import numpy as np
import pytest

from api import usrp_resolve
from api.allstar_audio import AllStarAudio, UsrpSettings
from api.dmr_audio import DmrAudio, DmrUsrpSettings
from api.service import RepeaterService
from controller.state_machine import RepeaterConfig
from link.usrp import decode

STALL = 0.6


def stalled_resolver(seconds, answer="127.0.0.1", error=None):
    """A `socket.getaddrinfo` that takes `seconds` for the service names. `socket.gethostbyname` is made to do the
    same, so these tests also describe the old code, which called it on the loop's thread."""
    real = socket.getaddrinfo

    def resolve(host, port, *args, **kwargs):
        if host in ("allstar", "dmr"):
            time.sleep(seconds)
            if error is not None:
                raise error
            return [(socket.AF_INET, socket.SOCK_DGRAM, 17, "", (answer, 0))]
        return real(host, port, *args, **kwargs)

    resolve.by_name = lambda host: resolve(host, None)[0][4][0]
    return resolve


def install(monkeypatch, resolver):
    monkeypatch.setattr(socket, "getaddrinfo", resolver)
    monkeypatch.setattr(socket, "gethostbyname", resolver.by_name)


class Ticker:
    """Wakes every 10 ms and remembers the longest gap between wakes: a blocked event loop shows up as a long gap."""

    def __init__(self):
        self.longest = 0.0
        self._task = None

    async def __aenter__(self):
        async def tick():
            loop = asyncio.get_running_loop()
            last = loop.time()
            while True:
                await asyncio.sleep(0.01)
                now = loop.time()
                self.longest = max(self.longest, now - last)
                last = now

        self._task = asyncio.create_task(tick())
        await asyncio.sleep(0.05)
        return self

    async def __aexit__(self, *exc):
        await asyncio.sleep(0.03)  # let the ticker wake once more, so a stall that just ended is counted
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)


def service():
    return RepeaterService(config=RepeaterConfig())


def allstar(host="allstar", port=32001):
    return AllStarAudio(service(), UsrpSettings("127.0.0.1", 0, host, port))


def dmr(host="dmr", port=34012):
    return DmrAudio(service(), DmrUsrpSettings("127.0.0.1", 0, host, port))


LINKS = pytest.mark.parametrize("make, ip_attr", [(allstar, "_node_ip"), (dmr, "_peer_ip")], ids=["allstar", "dmr"])


@LINKS
def test_a_slow_lookup_does_not_stop_the_event_loop(monkeypatch, make, ip_attr):
    install(monkeypatch, stalled_resolver(STALL))

    async def scenario():
        link = make()
        async with Ticker() as ticker:
            await link.start()
        assert getattr(link, ip_attr) == "127.0.0.1" and link.error is None and link._task is not None
        assert ticker.longest < STALL / 3, f"the loop was stopped for {ticker.longest:.2f} s"
        await link.stop()

    asyncio.run(scenario())


@LINKS
def test_a_lookup_that_never_answers_gives_up_with_the_same_kind_of_error_and_the_loop_keeps_running(monkeypatch, make, ip_attr):
    install(monkeypatch, stalled_resolver(1.5))
    monkeypatch.setattr(usrp_resolve, "RESOLVE_TIMEOUT_SECONDS", 0.2)

    async def scenario():
        link = make()
        async with Ticker() as ticker:
            await link.start()
        assert link._task is None and link.error and "took more than 0.2 s" in link.error
        assert ticker.longest < 0.15
        await link.stop()  # still safe to call

    asyncio.run(scenario())


@LINKS
def test_a_name_that_does_not_resolve_is_the_same_error_as_before(monkeypatch, make, ip_attr):
    install(monkeypatch, stalled_resolver(0, error=socket.gaierror(socket.EAI_NONAME, "Name or service not known")))

    async def scenario():
        link = make()
        await link.start()
        assert link._task is None and link._transport is None and getattr(link, ip_attr) is None
        assert "Name or service not known" in link.error

    asyncio.run(scenario())


def test_the_messages_are_the_ones_the_pages_already_show(monkeypatch):
    failure = socket.gaierror(socket.EAI_NONAME, "no such host")  # what gethostbyname raised too: "[Errno 8] no such host"
    install(monkeypatch, stalled_resolver(0, error=failure))

    async def scenario():
        node, bridge = allstar(), dmr()
        await node.start()
        await bridge.start()
        assert node.error == f"couldn't set up USRP on 127.0.0.1:0 for the node at allstar: {failure}"
        assert bridge.error.startswith(f"The DMR audio link could not start ({failure}).")

    asyncio.run(scenario())


class Listener(asyncio.DatagramProtocol):
    def __init__(self):
        self.received = asyncio.Queue()

    def datagram_received(self, data, addr):
        self.received.put_nowait(decode(data))


@LINKS
def test_audio_goes_to_the_address_that_was_looked_up_not_to_the_name(monkeypatch, make, ip_attr):
    """`sendto` with a host name resolves it again, on the loop's thread, on every 20 ms frame."""
    install(monkeypatch, stalled_resolver(0))

    async def scenario():
        loop = asyncio.get_running_loop()
        listener = Listener()
        transport, _ = await loop.create_datagram_endpoint(lambda: listener, local_addr=("127.0.0.1", 0))
        port = transport.get_extra_info("sockname")[1]
        link = make(port=port)
        await link.start()
        # After the lookup, the name must never be resolved again: any further lookup of it would fail here.
        install(monkeypatch, stalled_resolver(0, error=socket.gaierror("must not resolve again")))
        shared = link._link
        for _ in range(3):
            shared.exchange(np.full(link.rate // 50, 0.25, dtype=np.float32), carrier=True)
        packet = await asyncio.wait_for(listener.received.get(), 2)
        assert len(packet.audio) > 0 and link.error is None
        await link.stop()
        transport.close()

    asyncio.run(scenario())
