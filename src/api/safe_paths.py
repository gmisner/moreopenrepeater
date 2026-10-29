"""File names that come from requests or backups, kept inside their folder."""
from __future__ import annotations

import os
from pathlib import Path


def child_path(directory: Path, name: str) -> Path:
    """`directory / name`, or KeyError if that would land outside `directory`.

    Rejects `..`, absolute names, and a final component that is a symlink
    pointing outside the folder (the prefix check alone would allow that).
    """
    base = os.path.abspath(directory)
    path = os.path.normpath(os.path.join(base, name))
    if not path.startswith(base + os.sep):
        raise KeyError(name)
    real_base = os.path.realpath(base)
    real_path = os.path.realpath(path)
    if real_path != real_base and not real_path.startswith(real_base + os.sep):
        raise KeyError(name)
    return Path(path)
