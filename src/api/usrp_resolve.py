"""Look up the AllStar node's or the DMR bridge's address without stopping the core's event loop.

In a container install the host is a service name (`allstar`, `dmr`). When that container is not running, a lookup on a
site with no internet can take seconds, and `socket.gethostbyname` does it on the event loop's own thread, which is what
the 10 s stall watchdog watches. `loop.getaddrinfo` runs it in a thread; the timeout keeps a lookup that never answers
from holding up the start of the audio link.
"""
from __future__ import annotations

import asyncio
import socket

RESOLVE_TIMEOUT_SECONDS = 3.0


async def resolve_ipv4(host: str) -> str:
    """The IPv4 address of `host`. Raises `OSError` when the lookup fails or takes longer than the timeout, which is what
    `socket.gethostbyname` raised (`socket.gaierror` is an `OSError`), so callers handle it the same way."""
    timeout = RESOLVE_TIMEOUT_SECONDS
    try:
        found = await asyncio.wait_for(
            asyncio.get_running_loop().getaddrinfo(host, None, family=socket.AF_INET, type=socket.SOCK_DGRAM), timeout
        )
    except asyncio.TimeoutError:
        raise OSError(f"looking up {host} took more than {timeout:g} s") from None
    return found[0][4][0]
