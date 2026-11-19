"""Structured logging is switched on by environment and leaves the existing handlers alone (#221, #229)."""
import json
import logging
import shutil
import socket
import tempfile
from pathlib import Path

import pytest

from api.log_hub import LogHub
from api.logging_config import LOGGER_NAME, configure_logging
from contracts import LogEvent
from observability.handlers import CollectorHandler, JsonLineHandler


@pytest.fixture(autouse=True)
def clean_logger():
    yield
    logger = logging.getLogger(LOGGER_NAME)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()


@pytest.fixture
def sock_dir():
    path = Path(tempfile.mkdtemp(prefix="mor-", dir="/tmp"))
    yield path
    shutil.rmtree(path, ignore_errors=True)


def kinds():
    """The handlers configure_logging manages (pytest adds its own to the same logger)."""
    handlers = logging.getLogger(LOGGER_NAME).handlers
    return sorted(type(h).__name__ for h in handlers if getattr(h, "_moreopenrepeater_managed", False))


def test_with_nothing_set_it_is_exactly_what_it_was(tmp_path, capsys):
    hub = LogHub()
    logger = configure_logging(tmp_path / "x.log", hub=hub, env={})
    logger.info("plain text still")
    assert kinds() == ["HubHandler", "RotatingFileHandler", "StreamHandler"]
    assert "INFO moreopenrepeater: plain text still" in capsys.readouterr().err
    assert "plain text still" in (tmp_path / "x.log").read_text()


def test_json_format_puts_events_on_stdout_and_keeps_the_text_file_and_hub(tmp_path, capsys):
    hub = LogHub()
    logger = configure_logging(tmp_path / "x.log", hub=hub, module="core", env={"MOREOPENREPEATER_LOG_FORMAT": "json"})
    child = logging.getLogger("moreopenrepeater.audio")
    child.info("audio engine started")
    out = capsys.readouterr()
    event = LogEvent.model_validate_json(out.out.strip())
    assert event.module == "core" and event.event == "legacy.audio" and event.message == "audio engine started"
    assert out.err == ""  # no second, text copy on the console
    assert "audio engine started" in (tmp_path / "x.log").read_text()  # the file the dashboard reads is unchanged
    assert any("audio engine started" in line.line for line in hub.since(0)[0])
    assert kinds() == ["HubHandler", "JsonLineHandler", "RotatingFileHandler"]
    assert logger is logging.getLogger(LOGGER_NAME)


def test_the_logd_socket_adds_a_sender_that_reaches_it(tmp_path, sock_dir):
    receiver = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    receiver.bind(str(sock_dir / "logd.sock"))
    receiver.settimeout(5)
    configure_logging(tmp_path / "x.log", module="api", env={"MOREOPENREPEATER_LOGD_SOCKET": str(sock_dir / "logd.sock")})
    logging.getLogger("moreopenrepeater.weather").warning("poll failed")
    event = LogEvent.model_validate(json.loads(receiver.recv(70_000)))
    assert event.module == "api" and event.event == "legacy.weather" and event.severity == 30
    assert "CollectorHandler" in kinds() and "StreamHandler" in kinds()  # text console, as no json format was asked for


def test_one_record_is_one_event_whichever_way_it_leaves(tmp_path, sock_dir, capsys):
    receiver = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    receiver.bind(str(sock_dir / "logd.sock"))
    receiver.settimeout(5)
    configure_logging(tmp_path / "x.log", env={
        "MOREOPENREPEATER_LOG_FORMAT": "json", "MOREOPENREPEATER_LOGD_SOCKET": str(sock_dir / "logd.sock"),
    })
    logging.getLogger("moreopenrepeater.a").info("one line")
    on_stdout = LogEvent.model_validate_json(capsys.readouterr().out.strip())
    to_collector = LogEvent.model_validate_json(receiver.recv(70_000))
    assert on_stdout.boot_id == to_collector.boot_id  # one process, one identity
    assert on_stdout.seq == to_collector.seq  # one record is one event, whichever way it leaves


def test_configuring_again_does_not_stack_handlers_or_leak_the_sender(tmp_path, sock_dir):
    env = {"MOREOPENREPEATER_LOGD_SOCKET": str(sock_dir / "logd.sock"), "MOREOPENREPEATER_LOG_FORMAT": "json"}
    configure_logging(tmp_path / "x.log", env=env)
    first = [h for h in logging.getLogger(LOGGER_NAME).handlers if isinstance(h, CollectorHandler)][0]
    configure_logging(tmp_path / "x.log", env=env)
    assert kinds() == ["CollectorHandler", "JsonLineHandler", "RotatingFileHandler"]
    assert not first.running  # the old sender was closed, not abandoned


@pytest.mark.parametrize("value", ["", "text", "JSONL", "yaml"])
def test_anything_but_json_means_text(tmp_path, value):
    configure_logging(tmp_path / "x.log", env={"MOREOPENREPEATER_LOG_FORMAT": value})
    assert "JsonLineHandler" not in kinds()


def test_json_is_accepted_in_any_case_and_with_spaces(tmp_path):
    configure_logging(tmp_path / "x.log", env={"MOREOPENREPEATER_LOG_FORMAT": " JSON "})
    assert isinstance(
        [h for h in logging.getLogger(LOGGER_NAME).handlers if isinstance(h, JsonLineHandler)][0], JsonLineHandler
    )


def test_an_unreachable_collector_does_not_stop_the_process_logging(tmp_path, capsys):
    logger = configure_logging(tmp_path / "x.log", env={"MOREOPENREPEATER_LOGD_SOCKET": "/tmp/mor-nobody-home.sock"})
    logger.error("still logged locally")
    assert "still logged locally" in (tmp_path / "x.log").read_text()
    assert "still logged locally" in capsys.readouterr().err
