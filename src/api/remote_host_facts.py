"""What the core can see of its host, for code on a worker thread (the support bundle) in an API that runs apart from the core.

Same shape as `RemoteMixer`: the route is a plain function on a worker thread, the core is asked from the API's event loop,
and the thread waits for the answer with a limit. Called from the event loop's own thread it would wait on itself, so it
refuses to.
"""
from __future__ import annotations

import asyncio
import concurrent.futures

from contracts import CoreHostFacts

#: A little longer than the core's own limit for the read, so the core's answer, its reason included, comes first.
REMOTE_HOST_FACTS_TIMEOUT_SECONDS = 6.0


class RemoteHostFacts:
    def __init__(self, remote, timeout: float = REMOTE_HOST_FACTS_TIMEOUT_SECONDS) -> None:
        self._remote = remote
        self._timeout = timeout

    def __call__(self) -> CoreHostFacts:
        """The core's answer. Raises `CoreUnavailable` (it could not be asked), `TimeoutError` (it was too slow) or
        `RuntimeError` (called on the event loop's thread, or before the core was reached)."""
        loop = self._remote.loop
        if loop is None:
            raise RuntimeError("the core has not been reached yet")
        try:
            on_the_loop = asyncio.get_running_loop() is loop
        except RuntimeError:
            on_the_loop = False
        if on_the_loop:  # waiting here would stop the loop that has to carry the answer
            raise RuntimeError("RemoteHostFacts cannot be called on the event loop's own thread")
        future = asyncio.run_coroutine_threadsafe(self._remote.host_facts(), loop)
        try:
            return future.result(self._timeout)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise TimeoutError("the core did not answer in time") from None
