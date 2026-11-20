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
- A time zone this system does not know (a hand-edited file, an image without zone
  data): replace it with "" (the container's own zone), log a warning, and keep going.
  The announcements would say the wrong hour, which beats a repeater that is down.
"""
from __future__ import annotations

import json
import logging
import math
from pathlib import Path

import dataclasses

from api.service.snapshot import config_from_snapshot
from controller.config import CONFIG_FIELDS, RepeaterConfig
from controller.station_clock import check_timezone

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


def check_config(config: RepeaterConfig, *, log: bool = True) -> tuple[RepeaterConfig, list[str]]:
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
    try:
        check_timezone(config.timezone)
    except ValueError:
        fixes["timezone"] = ""
        notes.append(f"timezone was {config.timezone!r}, not a time zone this system knows; using the container's own")
    if fixes:
        config = config.replace_flat(**fixes)
    for note in notes if log else ():
        _logger.warning("saved configuration: %s", note)
    return config, notes


def load_config(path: Path) -> tuple[RepeaterConfig, list[str]]:
    """The saved configuration at `path`, checked."""
    return check_config(config_from_snapshot(read_snapshot(path)))


class ConfigRejected(Exception):
    """A change the core will not apply. `code` is stable; `message` names settings, never values."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _type_ok(current: object, value: object, optional: bool) -> bool:
    if value is None:
        return optional or current is None
    if isinstance(current, bool):
        return isinstance(value, bool)
    if isinstance(current, (int, float)):
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if isinstance(current, str):
        # An enum that is also a string (a style, a mode) arrives as its text.
        return isinstance(value, str)
    if isinstance(current, (list, dict)):
        return isinstance(value, type(current))
    return True  # nothing here the core can compare safely; the checks below still run


def check_change(current: RepeaterConfig, values: dict) -> RepeaterConfig:
    """The configuration that results from changing `current` by `values`, or `ConfigRejected`.

    This is the core's own check of a change that arrives over the control socket, so a bug
    or a hand-written client cannot hand the controller a zero time-out. It is stricter than
    the start-up check: a start-up repairs an unsafe number to keep the repeater up, but a
    change that asks for one is refused and nothing is applied."""
    unknown = sorted(k for k in values if k not in CONFIG_FIELDS)
    if unknown:
        raise ConfigRejected("config.unknown_setting", f"not a setting: {', '.join(unknown)}")
    annotations = {f.name: str(f.type) for f in dataclasses.fields(RepeaterConfig)}
    wrong = sorted(
        k for k, v in values.items()
        if not _type_ok(getattr(current, k), v, "Optional" in annotations[k] or "None" in annotations[k])
    )
    if wrong:
        raise ConfigRejected("config.wrong_type", f"wrong kind of value for: {', '.join(wrong)}")
    if "timezone" in values:
        try:
            check_timezone(values["timezone"])
        except ValueError:
            raise ConfigRejected(
                "config.invalid_timezone", "timezone is not a zone name this system knows (use one like America/Phoenix)"
            ) from None
    try:
        candidate = current.replace_flat(**values)
        checked, notes = check_config(candidate, log=False)
    except CoreConfigError as error:
        raise ConfigRejected("config.unsafe", str(error)) from None
    except (TypeError, ValueError):
        raise ConfigRejected("config.invalid", "the settings could not be combined into a configuration") from None
    if notes:
        names = sorted(k for k in values if k in _POSITIVE + _NON_NEGATIVE)
        raise ConfigRejected("config.unsafe", f"not a usable number: {', '.join(names)}")
    return checked
