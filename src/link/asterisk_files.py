"""Reading and writing Asterisk's config files (see link.asl_config).

On a Pi the controller's user is in the `asterisk` group and the files it
edits are group-writable (scripts/install-pi.sh sets this up). For
development with Asterisk in a VM, MOREOPENREPEATER_ASTERISK_SHELL is a
command prefix that runs `cat` and `tee` there, e.g.
`limactl shell asl3 -- sudo`.

Every file is copied into `backup_dir` before it's changed.
"""
from __future__ import annotations

import shlex
import subprocess
import time
from pathlib import Path
from typing import Optional

KEEP_BACKUPS = 10
TIMEOUT = 15


class AsteriskFilesError(Exception):
    pass


class AsteriskFiles:
    def __init__(self, directory: str = "/etc/asterisk", shell: Optional[list[str]] = None, backup_dir: Optional[Path] = None) -> None:
        self.directory = directory
        self.shell = shell
        self.backup_dir = backup_dir

    def _path(self, name: str) -> str:
        if name.startswith("/") or ".." in name.split("/"):
            raise ValueError(name)
        return f"{self.directory.rstrip('/')}/{name}"

    def read(self, name: str) -> str:
        path = self._path(name)
        if self.shell is None:
            try:
                return Path(path).read_text()
            except OSError as error:
                raise AsteriskFilesError(f"Couldn't read {path}: {error.strerror or error}")
        return self._run(["cat", path]).decode()

    def write(self, name: str, text: str) -> None:
        self._backup(name)
        path = self._path(name)
        if self.shell is None:
            try:
                # In place, not replaced: the directory isn't ours, and the
                # file keeps its owner and permissions.
                with open(path, "r+") as file:
                    file.write(text)
                    file.truncate()
            except OSError as error:
                raise AsteriskFilesError(
                    f"Couldn't write {path}: {error.strerror or error}. See docs/allstar.md for the permissions it needs."
                )
            return
        self._run(["tee", path], stdin=text.encode())

    def exists(self, name: str) -> bool:
        try:
            self.read(name)
            return True
        except AsteriskFilesError:
            return False

    def _backup(self, name: str) -> None:
        if self.backup_dir is None:
            return
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        stem = name.replace("/", "_")
        (self.backup_dir / f"{stem}.{time.strftime('%Y%m%d-%H%M%S')}").write_text(self.read(name))
        for old in sorted(self.backup_dir.glob(f"{stem}.*"))[:-KEEP_BACKUPS]:
            old.unlink()

    def _run(self, command: list[str], stdin: Optional[bytes] = None) -> bytes:
        assert self.shell is not None
        try:
            result = subprocess.run([*self.shell, *command], input=stdin, capture_output=True, timeout=TIMEOUT)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise AsteriskFilesError(f"Couldn't run {shlex.join(self.shell)}: {error}")
        if result.returncode != 0:
            raise AsteriskFilesError(f"{shlex.join(command)} failed: {result.stderr.decode(errors='replace').strip()}")
        return result.stdout


def asterisk_files_from_env(env: dict, backup_dir: Optional[Path]) -> Optional[AsteriskFiles]:
    directory = env.get("MOREOPENREPEATER_ASTERISK_CONFIG_DIR", "/etc/asterisk")
    shell = env.get("MOREOPENREPEATER_ASTERISK_SHELL")
    if shell:
        return AsteriskFiles(directory, shlex.split(shell), backup_dir)
    if not Path(directory, "rpt.conf").exists():
        return None
    return AsteriskFiles(directory, None, backup_dir)
