"""Write (or check) the published JSON Schemas under docs/contracts/schema.

    python -m contracts.schema          # rewrite the files
    python -m contracts.schema --check  # exit 1 if a field was added, removed or made required

`--check` compares field names and which are required, not the whole file.
Pydantic words other parts of the schema differently from release to release,
and that must not fail CI.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from .commands import CommandResult, CoreCommand
from .common import Header, StructuredError
from .configuration import ActivityRows, ConfigApplied, ConfigChange, ConfigSnapshot, ConfigState, CoreHardware, CoreLogs, LinkEvents, ProgrammingState
from .events import core_event_adapter
from .health import HealthReport
from .log_event import LogEvent
from .manifest import ModuleAnnounce
from .status import CoreStatus, SiteLocation
from .transmit import SpeakDecision, SpeakRequest

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "docs" / "contracts" / "schema"

MODELS = {
    "header.v1.json": Header,
    "error.v1.json": StructuredError,
    "log_event.v1.json": LogEvent,
    "speak_request.v1.json": SpeakRequest,
    "speak_decision.v1.json": SpeakDecision,
    "health_report.v1.json": HealthReport,
    "module_announce.v1.json": ModuleAnnounce,
    "config_snapshot.v1.json": ConfigSnapshot,
    "config_applied.v1.json": ConfigApplied,
    "config_state.v1.json": ConfigState,
    "config_change.v1.json": ConfigChange,
    "programming_state.v1.json": ProgrammingState,
    "activity_rows.v1.json": ActivityRows,
    "link_events.v1.json": LinkEvents,
    "core_logs.v1.json": CoreLogs,
    "core_hardware.v1.json": CoreHardware,
    "core_command.v1.json": CoreCommand,
    "core_command_result.v1.json": CommandResult,
    "core_status.v1.json": CoreStatus,
    "site_location.v1.json": SiteLocation,
}


def render() -> dict[str, str]:
    schemas = {name: model.model_json_schema() for name, model in MODELS.items()}
    schemas["core_event.v1.json"] = core_event_adapter.json_schema()
    return {name: json.dumps(schema, indent=2, sort_keys=True) + "\n" for name, schema in schemas.items()}


def _shape(text: str) -> dict:
    schema = json.loads(text)
    return {
        "properties": sorted(schema.get("properties", {})),
        "required": sorted(schema.get("required", [])),
        "kinds": sorted(schema.get("discriminator", {}).get("mapping", {})),
    }


def main(argv: list[str]) -> int:
    rendered = render()
    if "--check" in argv:
        stale = [
            name for name, text in rendered.items()
            if not (SCHEMA_DIR / name).is_file() or _shape((SCHEMA_DIR / name).read_text()) != _shape(text)
        ]
        for name in stale:
            print(f"out of date: {SCHEMA_DIR / name}", file=sys.stderr)
        return 1 if stale else 0
    SCHEMA_DIR.mkdir(parents=True, exist_ok=True)
    for name, text in rendered.items():
        (SCHEMA_DIR / name).write_text(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
