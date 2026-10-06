"""File names that come from requests or backups, kept inside their folder."""
from __future__ import annotations

import os
from pathlib import Path


def child_path(directory: Path, name: str) -> Path:
    """`directory / name`, or KeyError if that would land outside `directory`."""
    base = os.path.abspath(directory)
    path = os.path.normpath(os.path.join(base, name))
    if not path.startswith(base + os.sep):
        raise KeyError(name)
    return Path(path)
