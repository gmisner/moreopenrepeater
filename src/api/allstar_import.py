"""Bringing an existing AllStarLink node over to the container node (docs/allstar.md, Importing a node).

The old node's /etc/asterisk is mounted read-only into the API container (`MOREOPENREPEATER_ALLSTAR_IMPORT_DIR`, empty
unless `setup.sh --import-allstar` named a folder). `link.asl_import` reads it; this shows what it found and, when asked,
writes the node into the container node's files with the same writers the node setup uses (`AllStarNode.configure_identity`),
so the backups, the Asterisk restart and the audio hookup are the ones every other change gets.

The registration password is read from the source again at apply time and goes straight to the writers. It is not in
what the preview returns, not in what apply returns, and not in a log line.
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import asdict
from typing import Optional

from link.asl_config import add_section_entries, effective, nodes, parse_sections, registration_password, set_section_setting
from link.asl_import import (
    LEFT,
    PLACEHOLDER_NODES,
    TAKEN,
    SourceNode,
    directory_from_env,
    read_password,
    scan,
)

from .allstar_node import AllStarNode, AllStarSetupError

_logger = logging.getLogger("moreopenrepeater.allstar_import")


class ImportRefused(AllStarSetupError):
    """The import can't go ahead; the message says why."""


class NotFound(ImportRefused):
    pass


class NeedsConfirm(ImportRefused):
    pass


class AllStarImporter:
    def __init__(self, node: AllStarNode, directory: Optional[str], source: Optional[str] = None) -> None:
        """`directory` is the source folder as this process sees it, `source` the same folder as the owner named it on
        the host (shown on the page only)."""
        self._node = node
        self._directory = directory
        self._source = source
        self._lock = asyncio.Lock()

    def _requested(self) -> bool:
        """Someone asked for an import: `setup.sh --import-allstar` names the folder (shown as `source`), or a folder with
        a node in it was mounted by hand. The empty folder compose mounts by default is not a request."""
        if self._directory is None:
            return False
        return bool(self._source) or os.path.exists(os.path.join(self._directory, "rpt.conf"))

    @property
    def _can_edit(self) -> bool:
        # In a split install without the node's volume the node is a stand-in whose attributes are all truthy.
        return isinstance(self._node, AllStarNode) and self._node.can_edit

    @classmethod
    def from_env(cls, node: AllStarNode, env) -> "AllStarImporter":
        return cls(node, directory_from_env(env), (env.get("MOREOPENREPEATER_ALLSTAR_IMPORT_SOURCE") or "").strip() or None)

    # -- the preview ------------------------------------------------------------

    async def preview(self) -> dict:
        """What was found and what applying would do. Reads files only."""
        result: dict = {
            "configured": self._requested(),
            "available": self._can_edit,
            "found": False,
            "source": self._source or self._directory,
            "files": [],
            "nodes": [],
            "problems": [],
            "will_take": list(TAKEN),
            "will_not_take": list(LEFT),
            "target": None,
        }
        if not self._requested():
            return result
        found = await asyncio.get_running_loop().run_in_executor(None, scan, self._directory)
        result["files"] = [asdict(f) for f in found.files]
        result["problems"] = list(found.problems)
        result["nodes"] = [_describe(n) for n in found.nodes]
        result["found"] = found.found
        if found.found and self._can_edit:
            try:
                result["target"] = await self._target([n.number for n in found.nodes])
            except AllStarSetupError as error:
                result["problems"].append(str(error))
        return result

    async def _target(self, numbers: list[str]) -> dict:
        """For each node that could be applied, what it would do to the container node's files as they are now."""
        rpt = await self._node.read_file("rpt.conf")
        reg = await self._read_optional("rpt_http_registrations.conf")
        iax = await self._read_optional("iax.conf")
        mine = nodes(rpt)
        return {n: _plan(mine, reg, iax, n) for n in numbers}

    async def _read_optional(self, name: str) -> str:
        try:
            return await self._node.read_file(name)
        except AllStarSetupError:
            return ""

    # -- applying it ------------------------------------------------------------

    async def apply(self, number: str, *, replace: bool = False, callsign: Optional[str] = None) -> dict:
        async with self._lock:
            if self._directory is None or not self._requested():
                raise NotFound("There is no folder to import a node from.")
            if not self._can_edit:
                raise ImportRefused("AllStarLink isn't ready yet. Start it first (see How to set this up on the AllStarLink page).")
            found = await asyncio.get_running_loop().run_in_executor(None, scan, self._directory)
            source = next((n for n in found.nodes if n.number == number), None)
            if source is None:
                raise NotFound(f"Node {number} isn't in the files that were found.")
            callsign = (callsign or source.callsign or "").strip().upper()
            if not callsign:
                raise ImportRefused("That node has no callsign in its ID. Enter the callsign to use.")

            rpt = await self._node.read_file("rpt.conf")
            reg = await self._read_optional("rpt_http_registrations.conf")
            iax = await self._read_optional("iax.conf")
            plan = _plan(nodes(rpt), reg, iax, number)
            if plan["state"] == "blocked":
                raise ImportRefused(plan["reason"])
            if plan["state"] == "replace" and not replace:
                raise NeedsConfirm(
                    f"This node already has {plan['existing']} set up. Importing {number} replaces its number, callsign "
                    "and registration. Confirm to go on; a copy of every file is kept first."
                )
            password = await asyncio.get_running_loop().run_in_executor(None, read_password, self._directory, number)

            snapshot = await self._node.snapshot("import")
            took = [f"Node number {number}", f"Callsign {callsign}"]
            took.append("Registration password" if password else "No registration password (add it under Edit node)")
            if source.startup_macro:
                took.append(f"Startup macro {source.startup_macro}")
            if source.links:
                took.append(f"{len(source.links)} allowed links")

            def edit(text: str) -> str:
                if source.startup_macro:
                    text = set_section_setting(text, number, "startup_macro", source.startup_macro)
                if source.links:
                    text = add_section_entries(text, _links_stanza(text, number), source.links)
                return text

            status = await self._node.configure_identity(
                plan.get("from_node", number), number, callsign, password=password, extra_rpt=edit, require_password=False
            )
        _logger.info("imported node %s from %s (%d allowed links)", number, self._source or "the import folder", len(source.links))
        return {"status": status, "took": took, "left": list(LEFT), "snapshot": snapshot.name if snapshot else None}


def _links_stanza(rpt: str, number: str) -> str:
    """The stanza the container node's links go in: the one its `nodes =` setting names, `nodes` if none."""
    return (effective(parse_sections(rpt), number, "nodes") or "nodes").strip() or "nodes"


def _describe(node: SourceNode) -> dict:
    """The preview's picture of a source node. No password, only whether there is one."""
    return {
        "number": node.number,
        "callsign": node.callsign,
        "allowed_links": len(node.links),
        "startup_macro": node.startup_macro,
        "has_registration": node.has_registration,
        "password_usable": node.password_usable,
        "radio": node.radio,
        "notes": node.notes,
        "suggested": node.suggested,
    }


def _registered(reg: str, iax: str, number: str) -> bool:
    return bool(registration_password(reg, number) or registration_password(iax, number))


def _plan(mine, reg: str, iax: str, number: str) -> dict:
    """Which of the container's stanzas becomes `number`, and whether that takes over a node someone set up.

    state: "new" (a stock placeholder becomes it), "same" (it is already there: applying again), "replace" (another node
    that isn't a placeholder, or that registers, is set up and would be renamed) or "blocked" (nothing to rename)."""
    if any(n.number == number for n in mine):
        return {"state": "same", "existing": number}
    if not mine:
        return {
            "state": "blocked",
            "existing": None,
            "reason": "This machine has no node to turn into this one yet. Start AllStarLink once so it creates its starting files (see How to set this up on the AllStarLink page).",
        }
    configured = [n for n in mine if n.number not in PLACEHOLDER_NODES or _registered(reg, iax, n.number)]
    if configured:
        chosen = next((n for n in configured if n.controlled), configured[0])
        return {"state": "replace", "existing": chosen.number, "from_node": chosen.number}
    chosen = next((n for n in mine if n.controlled), None) or next(n for n in mine if n.number in PLACEHOLDER_NODES)
    return {"state": "new", "existing": chosen.number, "from_node": chosen.number}
