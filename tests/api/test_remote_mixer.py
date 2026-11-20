"""amixer for an API that runs apart from the core: the Audio page's levels and the board presets, in the container install."""
import asyncio
import subprocess
import threading

import pytest

from api.remote_mixer import RemoteMixer
from api.service.core_handle import CommandRefused, CoreUnavailable

SET = ["amixer", "-q", "-c", "2", "sset", "Speaker", "75%"]


class LoopThread:
    """An event loop on its own thread, like the API's, with the calls into it coming from a worker thread, like the routes'."""

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)
        self.loop.close()


@pytest.fixture
def loop():
    thread = LoopThread()
    yield thread
    thread.close()


class FakeRemote:
    def __init__(self, loop, result=None, error=None, delay=0.0):
        self.loop = loop.loop if loop is not None else None
        self.result, self.error, self.delay = result, error, delay
        self.asked = []

    async def mixer(self, args):
        self.asked.append(args)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return self.result


def test_what_the_core_printed_comes_back_as_a_finished_process(loop):
    remote = FakeRemote(loop, {"returncode": 0, "stdout": "Simple mixer control 'Speaker',0\n", "stderr": ""})
    done = RemoteMixer(remote)(["amixer", "-c", "2", "scontents"])
    assert (done.returncode, done.stdout, done.stderr) == (0, "Simple mixer control 'Speaker',0\n", "")
    assert remote.asked == [["-c", "2", "scontents"]]  # the program's name stays here


def test_a_failure_in_amixer_keeps_its_return_code_and_words(loop):
    remote = FakeRemote(loop, {"returncode": 1, "stdout": "", "stderr": "amixer: Unable to find simple control 'Speaker',0\n"})
    done = RemoteMixer(remote)(SET)
    assert done.returncode == 1 and "Unable to find" in done.stderr


@pytest.mark.parametrize("error", [CommandRefused("command.hardware_refused", "amixer isn't installed in the core's container"), CoreUnavailable("the core is not reachable")])
def test_a_core_that_refuses_or_is_away_is_said_the_way_amixer_would_say_it(loop, error):
    done = RemoteMixer(FakeRemote(loop, error=error))(SET)
    assert done.returncode == 1 and str(error) in done.stderr  # the routes show the last line of stderr


def test_before_the_core_has_been_reached_there_is_nothing_to_ask():
    done = RemoteMixer(FakeRemote(None))(SET)
    assert done.returncode == 1 and "not been reached" in done.stderr


def test_a_core_that_never_answers_is_a_timeout_like_a_hung_amixer(loop):
    remote = FakeRemote(loop, {"returncode": 0, "stdout": "", "stderr": ""}, delay=5)
    with pytest.raises(subprocess.TimeoutExpired):
        RemoteMixer(remote, timeout=0.2)(SET)


def test_it_only_ever_runs_amixer(loop):
    with pytest.raises(ValueError):
        RemoteMixer(FakeRemote(loop, {}))(["sh", "-c", "id"])
    with pytest.raises(ValueError):
        RemoteMixer(FakeRemote(loop, {}))([])


def test_called_on_the_event_loops_own_thread_it_refuses_instead_of_freezing_the_loop(loop):
    remote = FakeRemote(loop, {"returncode": 0, "stdout": "", "stderr": ""})
    outcome = []

    async def on_the_loop():
        try:
            RemoteMixer(remote)(SET)
        except RuntimeError as error:
            outcome.append(str(error))

    asyncio.run_coroutine_threadsafe(on_the_loop(), loop.loop).result(5)
    assert outcome and "event loop" in outcome[0]
