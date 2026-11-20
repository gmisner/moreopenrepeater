"""`amixer` for an API that runs apart from the core.

The sound card's levels are read and set with `amixer`. In the container install the API has neither the program nor the
sound card; the core has both. So the Audio page's sliders and the board presets call this instead of `run_amixer`: it
sends the arguments to the core (`mixer_run`) and hands back what `amixer` printed, shaped like the `subprocess` result
the callers already expect. The callers run on worker threads (never on the event loop), so waiting for the core here
holds nothing else up.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import subprocess

from .service.core_handle import CommandRefused, CoreUnavailable

#: A little longer than the core's own limit for `amixer`, so the core's answer comes first when it is slow.
REMOTE_MIXER_TIMEOUT_SECONDS = 20.0


class RemoteMixer:
    def __init__(self, remote, timeout: float = REMOTE_MIXER_TIMEOUT_SECONDS) -> None:
        self._remote = remote
        self._timeout = timeout

    def __call__(self, args: list[str]) -> subprocess.CompletedProcess:
        if not args or args[0] != "amixer":
            raise ValueError("RemoteMixer only runs amixer")
        loop = self._remote.loop
        if loop is None:
            return subprocess.CompletedProcess(args, 1, "", "the core has not been reached yet")
        try:
            on_the_loop = asyncio.get_running_loop() is loop
        except RuntimeError:
            on_the_loop = False
        if on_the_loop:  # waiting here would stop the loop that has to carry the answer
            raise RuntimeError("RemoteMixer cannot be called on the event loop's own thread")
        future = asyncio.run_coroutine_threadsafe(self._remote.mixer(args[1:]), loop)
        try:
            output = future.result(self._timeout)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise subprocess.TimeoutExpired(args, self._timeout) from None
        except (CommandRefused, CoreUnavailable) as error:  # said as amixer would say it, so the callers' own messages work
            return subprocess.CompletedProcess(args, 1, "", str(error))
        return subprocess.CompletedProcess(args, output["returncode"], output["stdout"], output["stderr"])
