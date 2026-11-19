import pytest

from api.logging_config import configure_logging


@pytest.fixture(autouse=True)
def _log_to_tmp(tmp_path):
    """Importing `api.app` points logging at data/, so tests without their own app would log there."""
    configure_logging(tmp_path / "test.log")


@pytest.fixture(autouse=True)
def _no_cycle_left_open():
    """An activity cycle is process-wide; one test's must not be stamped on the next test's events."""
    from observability import end_cycle

    end_cycle()
    yield
    end_cycle()


class CapturedEvents(list):
    """The structured events logged while a test ran, in order."""

    def named(self, name):
        return [event for event in self if event.event == name]

    def names(self):
        return [event.event for event in self]


@pytest.fixture
def structured_events():
    import logging

    from observability.events import ProcessIdentity, record_to_event, snapshot, stamp

    identity = ProcessIdentity("core", "test")
    captured = CapturedEvents()

    class Collect(logging.Handler):
        def emit(self, record):
            captured.append(record_to_event(snapshot(record), identity, stamp(record, identity)))

    handler = Collect(level=logging.DEBUG)
    logger = logging.getLogger("moreopenrepeater")
    level = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    yield captured
    logger.removeHandler(handler)
    logger.setLevel(level)
