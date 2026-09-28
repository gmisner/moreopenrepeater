"""Logging setup: a rotating file handler plus console output.

Every repeater controller has this kind of operational log -- it was
conspicuously absent from this project until now. All modules log under the
"moreopenrepeater" logger (or a child of it, e.g. "moreopenrepeater.controller"),
which is configured here rather than left to logging defaults.
"""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOGGER_NAME = "moreopenrepeater"


def configure_logging(log_path: Path, level: int = logging.INFO) -> logging.Logger:
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

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    file_handler = RotatingFileHandler(log_path, maxBytes=2_000_000, backupCount=3)
    file_handler.setFormatter(formatter)
    file_handler._moreopenrepeater_managed = True  # type: ignore[attr-defined]
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    stream_handler._moreopenrepeater_managed = True  # type: ignore[attr-defined]
    logger.addHandler(stream_handler)

    return logger
