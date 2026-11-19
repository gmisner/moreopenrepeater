"""Logging setup: a rotating file handler plus console output.

Every repeater controller has this kind of operational log -- it was
conspicuously absent from this project until now. All modules log under the
"moreopenrepeater" logger (or a child of it, e.g. "moreopenrepeater.controller"),
which is configured here rather than left to logging defaults.

When a LogHub is given, each formatted line is also pushed there for the live
SSE stream, MCP cursor polls, and optional remote shipping.
"""
from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Mapping, Optional

from observability import CollectorHandler, JsonLineHandler, ProcessIdentity

from .log_hub import HubHandler, LogHub

LOGGER_NAME = "moreopenrepeater"
LOG_FORMAT_ENV = "MOREOPENREPEATER_LOG_FORMAT"  # "json": one structured event per line on stdout, for a container's log driver
LOGD_SOCKET_ENV = "MOREOPENREPEATER_LOGD_SOCKET"  # send each event to the logd collector on this Unix socket
MODULE_VERSION = "0.1.0"


def configure_logging(
    log_path: Path,
    level: int = logging.INFO,
    hub: Optional[LogHub] = None,
    *,
    module: str = "api",
    env: Optional[Mapping[str, str]] = None,
) -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # Idempotent: drop any handlers this function previously attached (e.g. a
    # prior create_app() call with a different log_path, as happens across
    # tests) instead of stacking duplicate handlers on the shared logger.
    for handler in list(logger.handlers):
        if getattr(handler, "_moreopenrepeater_managed", False):
            logger.removeHandler(handler)
            handler.close()  # a collector sender has a thread to stop

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    file_handler = RotatingFileHandler(log_path, maxBytes=2_000_000, backupCount=3)
    file_handler.setFormatter(formatter)
    file_handler._moreopenrepeater_managed = True  # type: ignore[attr-defined]
    logger.addHandler(file_handler)

    env = os.environ if env is None else env
    identity = ProcessIdentity(module, MODULE_VERSION)  # one identity: the same boot id and counter for every way out
    # The console copy is text for a person at a terminal, or JSON events for a container's log driver.
    if (env.get(LOG_FORMAT_ENV) or "").strip().lower() == "json":
        stream_handler: logging.Handler = JsonLineHandler(identity)
    else:
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
    stream_handler._moreopenrepeater_managed = True  # type: ignore[attr-defined]
    logger.addHandler(stream_handler)

    collector = (env.get(LOGD_SOCKET_ENV) or "").strip()
    if collector:
        collector_handler = CollectorHandler(identity, Path(collector))
        collector_handler._moreopenrepeater_managed = True  # type: ignore[attr-defined]
        logger.addHandler(collector_handler)

    if hub is not None:
        hub_handler = HubHandler(hub)
        hub_handler.setFormatter(formatter)
        hub_handler._moreopenrepeater_managed = True  # type: ignore[attr-defined]
        logger.addHandler(hub_handler)

    return logger
