import pytest

from api.logging_config import configure_logging


@pytest.fixture(autouse=True)
def _log_to_tmp(tmp_path):
    """Importing `api.app` points logging at data/, so tests without their own app would log there."""
    configure_logging(tmp_path / "test.log")
