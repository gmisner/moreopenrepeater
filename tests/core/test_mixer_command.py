"""`mixer_run`: the core runs amixer for the API, which has neither the program nor the sound card (container install)."""
import asyncio
import json
import subprocess

import pytest
from control_helpers import make_server
from test_control_command import make_command, post_command

from core.build import build_core

READ = ["-c", "2", "scontents"]
SET = ["-q", "-c", "2", "sset", "Speaker", "75%"]


@pytest.fixture
def core(tmp_path):
    (tmp_path / "state.json").write_text(json.dumps({"config": {"callsign": "W1AW", "audio_enabled": False}}))
    return build_core({"MOREOPENREPEATER_DATA_DIR": str(tmp_path), "MOREOPENREPEATER_LOG_PATH": str(tmp_path / "c.log")})


def send(core, sock_dir, args):
    async def go():
        path = sock_dir / "core.sock"
        server = make_server(core, path)
        await server.start()
        try:
            return (await post_command(path, make_command("mixer_run", args=args)))[1]
        finally:
            await server.stop()

    return asyncio.run(go())


def test_it_runs_amixer_with_the_arguments_and_hands_back_what_it_printed(core, sock_dir):
    calls = []

    def amixer(args):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "Simple mixer control 'Speaker',0\n", "")

    core.runtime.run_mixer = amixer
    body = send(core, sock_dir, READ)
    assert body["status"] == "applied"
    assert body["output"] == {"returncode": 0, "stdout": "Simple mixer control 'Speaker',0\n", "stderr": ""}
    assert calls == [["amixer", *READ]]  # the program is the core's to name, not the client's


def test_a_setting_is_run_the_same_way_and_a_failure_comes_back_as_amixer_said_it(core, sock_dir):
    core.runtime.run_mixer = lambda args: subprocess.CompletedProcess(
        args, 1, "", "amixer: Unable to find simple control 'Speaker',0\n"
    )
    body = send(core, sock_dir, SET)
    assert body["status"] == "applied"  # the command ran; what amixer made of it is in the output
    assert body["output"]["returncode"] == 1 and "Unable to find" in body["output"]["stderr"]


def test_an_amixer_that_is_not_installed_is_refused_in_words(core, sock_dir):
    def missing(args):
        raise FileNotFoundError("amixer")

    core.runtime.run_mixer = missing
    body = send(core, sock_dir, READ)
    assert body["status"] == "rejected" and "isn't installed" in body["error"]["message"]
    assert body["error"]["code"] == "command.hardware_refused"


def test_an_amixer_that_hangs_is_refused_and_may_be_tried_again(core, sock_dir):
    def hang(args):
        raise subprocess.TimeoutExpired(args, 15)

    core.runtime.run_mixer = hang
    body = send(core, sock_dir, READ)
    assert body["status"] == "rejected" and "didn't respond" in body["error"]["message"] and body["error"]["retryable"] is True


def test_anything_unexpected_is_a_refusal_not_a_crash_and_says_nothing_of_the_cause(core, sock_dir):
    def broken(args):
        raise RuntimeError("secret detail")

    core.runtime.run_mixer = broken
    body = send(core, sock_dir, READ)
    assert body["status"] == "rejected" and body["error"]["code"] == "command.apply_failed"
    assert "secret detail" not in json.dumps(body)


def test_what_it_prints_is_cut_at_a_limit_so_one_card_cannot_fill_the_socket(core, sock_dir):
    from core import control

    core.runtime.run_mixer = lambda args: subprocess.CompletedProcess(args, 0, "x" * (control.MAX_MIXER_OUTPUT_CHARS * 3), "e" * 10)
    body = send(core, sock_dir, READ)
    assert len(body["output"]["stdout"]) == control.MAX_MIXER_OUTPUT_CHARS and body["output"]["stderr"] == "e" * 10


def test_the_real_amixer_is_what_the_core_runs_by_default(core):
    from api.boards import run_amixer

    assert core.runtime.run_mixer is run_amixer
