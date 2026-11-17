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

from .common import Header, StructuredError
from .log_event import LogEvent

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "docs" / "contracts" / "schema"

MODELS = {
    "header.v1.json": Header,
    "error.v1.json": StructuredError,
    "log_event.v1.json": LogEvent,
}


def render() -> dict[str, str]:
    return {
        name: json.dumps(model.model_json_schema(), indent=2, sort_keys=True) + "\n"
        for name, model in MODELS.items()
    }


def _shape(text: str) -> dict:
    schema = json.loads(text)
    return {"properties": sorted(schema.get("properties", {})), "required": sorted(schema.get("required", []))}


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
