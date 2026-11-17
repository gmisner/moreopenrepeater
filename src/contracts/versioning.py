"""Contract version rules.

Every message carries `schema_version` (an integer, bumped on a breaking change
to that message). The contract set as a whole has a `major.minor` version that
a module manifest declares. Within one major version, adding optional fields
is a minor change; removing or retyping a field, or changing what a field
means, is a new major.
"""
from __future__ import annotations

CONTRACT_VERSION = (1, 3)


def parse_version(text: str) -> tuple[int, int]:
    """Parse "1.2" into (1, 3). Raises ValueError on anything else."""
    parts = text.split(".")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        raise ValueError(f"contract version must look like '1.0', got {text!r}")
    return int(parts[0]), int(parts[1])


def is_compatible(module: tuple[int, int], core: tuple[int, int] = CONTRACT_VERSION) -> bool:
    """A module works with a core when the majors match and the module does not
    need a newer minor than the core offers."""
    return module[0] == core[0] and module[1] <= core[1]
