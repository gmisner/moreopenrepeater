"""Stopping the API's client for the core ends its tasks even when one swallows the cancel (#311).

On Python 3.11 `asyncio.wait_for` can swallow a cancel that lands in the same loop turn as the line it was
waiting for. The stream task then carries on, and `stop()` waited on it forever. CI hung for hours on that, in
a test that stops the client while the core's heartbeats are still arriving.
"""
import asyncio

from api.remote_core import RemoteCore


def test_stop_does_not_wait_forever_on_a_task_that_swallows_the_first_cancel(tmp_path):
    async def go():
        remote = RemoteCore(tmp_path / "core.sock")
        swallowed = []

        async def stubborn():
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                swallowed.append(1)  # what wait_for does on 3.11 when the line and the cancel arrive together
            await asyncio.sleep(3600)

        task = remote._task = asyncio.ensure_future(stubborn())
        await asyncio.sleep(0)  # let it start waiting
        stopping = asyncio.ensure_future(remote.stop())
        # asyncio.wait, not wait_for: stop() swallows a cancel of its own, so a wait_for timeout would not show.
        done, _ = await asyncio.wait({stopping}, timeout=3)
        try:
            assert done, "stop() is still waiting on a task that ignored the cancel"
            assert swallowed == [1] and task.done() and remote._task is None
        finally:
            task.cancel()
            stopping.cancel()
            await asyncio.gather(task, stopping, return_exceptions=True)

    asyncio.run(go())
