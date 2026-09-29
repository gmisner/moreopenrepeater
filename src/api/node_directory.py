"""Callsigns and descriptions for AllStarLink node numbers.

AllStarLink publishes every registered node as `node|callsign|description|
location` lines (the list Allmon and Supermon use). It's downloaded once a
day into the data folder and looked up in memory; without internet the last
copy keeps working, and without any copy nodes are shown by number only.
"""
from __future__ import annotations

import asyncio
import logging
import time
import urllib.request
from pathlib import Path
from typing import Callable, Optional

_logger = logging.getLogger("moreopenrepeater.nodes")

URL = "https://allmondb.allstarlink.org/"
MAX_AGE_SECONDS = 24 * 3600
RETRY_SECONDS = 3600
MAX_BYTES = 20_000_000


def _download(url: str = URL) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "moreopenrepeater"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read(MAX_BYTES)


def parse(text: str) -> dict[str, dict]:
    nodes = {}
    for line in text.splitlines():
        fields = line.split("|")
        if len(fields) >= 2 and fields[0].isdigit():
            nodes[fields[0]] = {
                "callsign": fields[1].strip(),
                "description": fields[2].strip() if len(fields) > 2 else "",
                "location": fields[3].strip() if len(fields) > 3 else "",
            }
    return nodes


class NodeDirectory:
    def __init__(self, path: Optional[Path], download: Callable[[], bytes] = _download, clock: Callable[[], float] = time.time) -> None:
        self._path = path
        self._download = download
        self._clock = clock
        self._nodes: dict[str, dict] = {}
        self._loaded_mtime: Optional[float] = None

    def lookup(self, node: str) -> Optional[dict]:
        return self._nodes.get(node)

    def __len__(self) -> int:
        return len(self._nodes)

    def load(self) -> None:
        """Read the saved copy, if there is one."""
        if self._path is None or not self._path.exists():
            return
        try:
            self._nodes = parse(self._path.read_text(errors="replace"))
            self._loaded_mtime = self._path.stat().st_mtime
        except OSError as error:
            _logger.warning("couldn't read the AllStarLink node list: %s", error)

    def stale(self) -> bool:
        return self._loaded_mtime is None or self._clock() - self._loaded_mtime > MAX_AGE_SECONDS

    def refresh(self) -> None:
        """Download a fresh copy (blocking)."""
        data = self._download()
        nodes = parse(data.decode("utf-8", errors="replace"))
        if not nodes:
            raise ValueError("the AllStarLink node list was empty")
        self._nodes = nodes
        self._loaded_mtime = self._clock()
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_bytes(data)
            tmp.replace(self._path)
        _logger.info("AllStarLink node list: %d nodes", len(nodes))

    async def run(self) -> None:
        """Keep the list fresh while the app runs."""
        await asyncio.to_thread(self.load)
        while True:
            if self.stale():
                try:
                    await asyncio.to_thread(self.refresh)
                except (OSError, ValueError) as error:
                    _logger.warning("couldn't download the AllStarLink node list: %s", error)
            await asyncio.sleep(RETRY_SECONDS)
