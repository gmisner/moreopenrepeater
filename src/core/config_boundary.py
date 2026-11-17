"""The core's own check of the configuration it starts from.

In the single-process service the dashboard validates every setting before it
is saved. A core running alone has no dashboard, and reads a file that the
dashboard may have written long ago, that a person may have edited by hand, or
that a bad update may have damaged. So the core checks it itself, and decides
what is safe:

- Missing, unreadable or unparsable file: refuse to start. Quietly running on
  defaults would put a repeater on the air with no callsign.
- A blank callsign: refuse to start. A core with no dashboard cannot be set up,
  and it must not transmit unidentified.
- A numeric setting that guards the transmitter (time-out, ID interval, hang
  time, link and autopatch limits) that is not a sane positive number: replace
  it with its default, log a warning, and keep going. A repeater that is up with
  a default time-out is safer than one that is down, and a zero or negative
  time-out would key the transmitter and drop it at once.
"""
from __future__ import annotations

import json
import logging
import math
from pathlib import Path

from api.service.snapshot import config_from_snapshot
from controller.config import RepeaterConfig

_logger = logging.getLogger("moreopenrepeater.core")


class CoreConfigError(Exception):
    """The core cannot start from this configuration. The message says what to do."""


# Setting -> the smallest value that is safe. `must_exceed` means strictly greater.
_POSITIVE = (
    "tot_duration",
    "id_interval",
    "net_tot_duration",
    "link_radio_timeout",
    "autopatch_max_call_seconds",
)
_NON_NEGATIVE = ("hang_time",)


def read_snapshot(path: Path) -> dict:
    """The saved state as a dict, or `CoreConfigError`. Never moves or rewrites the file."""
    try:
        text = path.read_text()
    except FileNotFoundError:
        raise CoreConfigError(
            f"no saved configuration at {path}. Start the normal service once and set the repeater up "
            "in the dashboard first; the core alone cannot be set up."
        ) from None
    except OSError as error:
        raise CoreConfigError(f"cannot read {path}: {error}") from error
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise CoreConfigError(
            f"{path} is not valid JSON ({error}). Restore it from a backup; the core will not start on defaults."
        ) from error
    if not isinstance(data, dict):
        raise CoreConfigError(f"{path} does not hold a saved configuration (expected an object).")
    return data


def _sane(value: object, *, allow_zero: bool) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    if not math.isfinite(value):
        return False
    return value >= 0 if allow_zero else value > 0


def check_config(config: RepeaterConfig) -> tuple[RepeaterConfig, list[str]]:
    """Returns the configuration to run with and a note for each setting replaced.
    Raises `CoreConfigError` if the core must not start."""
    if not (config.callsign or "").strip():
        raise CoreConfigError(
            "the saved configuration has no callsign. Set one in the dashboard (start the normal service) "
            "before running the core alone."
        )
    defaults = RepeaterConfig()
    fixes: dict[str, object] = {}
    notes: list[str] = []
    for name, allow_zero in [(n, False) for n in _POSITIVE] + [(n, True) for n in _NON_NEGATIVE]:
        value = getattr(config, name)
        if not _sane(value, allow_zero=allow_zero):
            fixes[name] = getattr(defaults, name)
            notes.append(f"{name} was {value!r}, not a usable number; using the default {fixes[name]!r}")
    if fixes:
        config = config.replace_flat(**fixes)
    for note in notes:
        _logger.warning("saved configuration: %s", note)
    return config, notes


def load_config(path: Path) -> tuple[RepeaterConfig, list[str]]:
    """The saved configuration at `path`, checked."""
    return check_config(config_from_snapshot(read_snapshot(path)))
