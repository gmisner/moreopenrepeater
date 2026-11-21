"""Reading an existing AllStarLink node's configuration so the container node can take it over.

Someone who already runs ASL3 on the repeater's computer has a node number, a callsign, a registration password and some
settings in /etc/asterisk. This reads those files (never writes them) and says what it found. What it takes is small on
purpose: the node number, the callsign from `idrecording`, the registration password, `startup_macro`, and the entries in
the node's `nodes` list. The radio is not taken: in the container the radio is the controller's core over USRP, so the
source's channel driver, sound card and PTT/COS settings (rxchannel, txchannel, duplex, simpleusb.conf, usbradio.conf,
voter.conf) are neither copied nor even read.

The files are read the way Asterisk reads them where it matters (comments, `;-- --;` blocks, `#include` and `#tryinclude`,
templates such as `[1999](node-main)`) and tolerantly everywhere else: nothing in here raises on odd input, a file that
can't be read is reported with the reason, and `#exec` is never run.

Passwords: `registration_password` is the only function that returns one, for the apply step. `scan` never holds one in
what it returns.
"""
from __future__ import annotations

import glob as _glob
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .asl_config import effective, parse_sections, registration_password

_logger = logging.getLogger("moreopenrepeater.asl_import")

MAX_FILE_BYTES = 1_000_000
MAX_INCLUDE_DEPTH = 4
MAX_INCLUDES = 40

# What the controller takes over from the source node, and what it deliberately leaves. Shown on the page.
TAKEN = (
    "Node number",
    "Callsign",
    "Registration password (saved safely and never shown)",
    "Startup macro",
    "Allowed links",
)
LEFT = (
    "Radio settings: this repeater is the node's radio now",
    "Sound card, PTT and COS settings",
    "The management login: this install has its own",
    "Other connections, such as IAX users and EchoLink",
)

# The names of the files this looks at, besides the ones rpt.conf includes.
RPT = "rpt.conf"
OPTIONAL_FILES = ("rpt_http_registrations.conf", "iax.conf", "extensions.conf", "savenode.conf")

_DIGITS = re.compile(r"^[0-9]{1,10}$")
_BLOCK_COMMENT = re.compile(r";--.*?(?:--;|\Z)", re.DOTALL)
_INCLUDE = re.compile(r"^\s*#\s*(?P<kind>include|tryinclude|exec)\b\s*(?P<arg>.*)$", re.IGNORECASE)
_QUOTED = re.compile(r"""^\s*(?:"(?P<d>[^"]*)"|'(?P<s>[^']*)'|<(?P<a>[^>]*)>|(?P<b>[^\s;]+))""")
_MACRO = re.compile(r"^[0-9*#A-Da-d]{1,64}$")
_CALLSIGN = re.compile(r"^[A-Z0-9][A-Z0-9/-]{1,10}[A-Z0-9]$")
_NODE_VAR = re.compile(r"^\s*NODE\s*=\s*(?P<node>[0-9]+)", re.IGNORECASE | re.MULTILINE)
_PASSWORD_OK = re.compile(r"^[0-9A-Za-z_-]{6,}$")
_SAVENODE_PASSWORD = re.compile(r"^\s*PASSWORD\s*=\s*(?P<password>\S+)", re.IGNORECASE | re.MULTILINE)
PLACEHOLDER_NODES = ("1999", "1998", "1234")


@dataclass
class FileReport:
    name: str
    status: str  # "read", "missing" or "unreadable"
    detail: str = ""
    included: list[str] = field(default_factory=list)


@dataclass
class SourceNode:
    number: str
    callsign: str  # "" when the node has no CW ID of its own
    startup_macro: str  # "" when it has none (or one that isn't a plain DTMF string)
    links: dict[str, str]  # the entries taken from its nodes list
    links_skipped: int  # entries left out because they point at the source machine itself
    has_registration: bool
    password_usable: bool  # a registration password was found and the dashboard can write it
    radio: str  # the source's rxchannel, shown as not copied
    notes: list[str] = field(default_factory=list)
    suggested: bool = False


@dataclass
class Scan:
    directory: str
    files: list[FileReport]
    nodes: list[SourceNode]
    problems: list[str]  # things that stop the import (no rpt.conf, no node in it)

    @property
    def found(self) -> bool:
        return bool(self.nodes)


# -- reading --------------------------------------------------------------------


def _read_text(path: Path) -> str:
    """The file as text, or an OSError. Odd bytes are replaced, not fatal."""
    size = path.stat().st_size
    if size > MAX_FILE_BYTES:
        raise OSError(0, f"is {size // 1024} KB, more than the {MAX_FILE_BYTES // 1000} KB this reads")
    return path.read_bytes().decode("utf-8", errors="replace")


def _why(error: OSError) -> str:
    if isinstance(error, PermissionError):
        return "permission denied (the container runs as user 999, which can't read it)"
    return error.strerror or str(error)


def _inside(root: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except (OSError, ValueError, RuntimeError):
        return False
    return True


def _include_targets(root: Path, argument: str) -> tuple[list[Path], str]:
    """The files an include names, which must be inside `root`, and why not when it names none. A `/etc/asterisk/...`
    path means the same place in the source folder, whatever the folder is called here."""
    match = _QUOTED.match(argument)
    wanted = next((g for g in match.groups() if g), "") if match else ""
    wanted = wanted.strip()
    if wanted.startswith("/etc/asterisk/"):
        wanted = wanted[len("/etc/asterisk/"):]
    if not wanted:
        return [], "an include with no file name was skipped"
    if wanted.startswith("/") or ".." in Path(wanted).parts:
        return [], f"{wanted} is outside the folder, so it was not read"
    try:
        hits = sorted(_glob.glob(str(root / wanted)))
    except (OSError, ValueError):
        return [], f"{wanted} couldn't be looked up"
    targets = [Path(hit) for hit in hits if Path(hit).is_file() and _inside(root, Path(hit))]
    return targets, "" if targets or hits else f"{wanted} isn't there"


def read_conf(root: Path, name: str) -> tuple[Optional[str], FileReport]:
    """One config file with its `;-- --;` comments cut out and its includes spliced in where they stand. Returns the
    text, or None when the file can't be had, and a report of what happened either way."""
    path = root / name
    report = FileReport(name, "read")
    try:
        text = _read_text(path)
    except FileNotFoundError:
        return None, FileReport(name, "missing")
    except OSError as error:
        return None, FileReport(name, "unreadable", _why(error))
    budget = [MAX_INCLUDES]
    failures: list[str] = []
    seen = {path.resolve()} if _inside(root, path) else set()

    def expand(body: str, depth: int) -> str:
        body = _BLOCK_COMMENT.sub("", body)
        out = []
        for line in body.splitlines():
            match = _INCLUDE.match(line)
            if not match:
                out.append(line)
                continue
            kind = match["kind"].lower()
            if kind == "exec":
                failures.append("an #exec line was not run")
                continue
            if depth >= MAX_INCLUDE_DEPTH:
                failures.append("includes nested too deep, the rest were skipped")
                continue
            targets, why = _include_targets(root, match["arg"])
            if why and (kind == "include" or "outside" in why):
                failures.append(why)  # a #tryinclude of a file that isn't there is normal and not worth a word
            for target in targets:
                if budget[0] <= 0:
                    failures.append("too many includes, the rest were skipped")
                    break
                resolved = target.resolve()
                if resolved in seen:
                    continue  # an include of a file already being read: a loop
                seen.add(resolved)
                budget[0] -= 1
                relative = os.path.relpath(target, root)
                try:
                    included = _read_text(target)
                except OSError as error:
                    failures.append(f"{relative} couldn't be read: {_why(error)}")
                    continue
                report.included.append(relative)
                out.append(expand(included, depth + 1))
        return "\n".join(out)

    try:
        merged = expand(text, 0)
    except RecursionError:
        return text, FileReport(name, "read", "its includes nest too deep to follow")
    if failures:
        report.detail = "; ".join(dict.fromkeys(failures))
    return merged, report


# -- understanding it -----------------------------------------------------------


def _callsign(sections, number: str) -> tuple[str, Optional[str]]:
    """(callsign, note). The CW ID is `idrecording = |iW1AW` (or `idtalkover`)."""
    for key in ("idrecording", "idtalkover"):
        value = (effective(sections, number, key) or "").strip()
        if value.startswith("|i"):
            candidate = value[2:].split("|")[0].strip().upper()
            if _CALLSIGN.match(candidate):
                return candidate, None
            return "", f"Its ID ({value}) isn't a callsign this can use."
        if value and value.upper() not in ("NOTSET", "NONE"):
            return "", f"Its ID is a recording ({value}), which isn't carried over."
    return "", None


def _link_entries(sections, number: str) -> tuple[dict[str, str], int]:
    """The entries of the node's `nodes` list (a stanza named by its `nodes =` setting, `nodes` if it has none),
    leaving out the node itself and any that point at the source machine, which would mean the container."""
    stanza = (effective(sections, number, "nodes") or "nodes").strip() or "nodes"
    section = next((s for s in sections if s.name == stanza), None)
    if section is None:
        return {}, 0
    taken: dict[str, str] = {}
    skipped = 0
    for key, value in section.settings.items():
        if not _DIGITS.match(key) or key == number:
            continue
        host = re.search(r"@\[?([^\]/:\s,]+)", value)
        name = host.group(1).lower() if host else ""
        if name in ("localhost", "::1") or name.startswith("127."):
            skipped += 1
            continue
        if not value or re.search(r"[\n\r]", value):
            continue
        taken[key] = value
    return taken, skipped


def password_for(texts: dict[str, Optional[str]], number: str) -> Optional[str]:
    """The registration password for `number` from whichever file has it first. Returned for the apply step only."""
    for name in ("rpt_http_registrations.conf", "iax.conf"):
        text = texts.get(name)
        if text:
            password = registration_password(text, number)
            if password:
                return password
    savenode = texts.get("savenode.conf")
    if savenode:
        node = _NODE_VAR.search(savenode)
        match = _SAVENODE_PASSWORD.search(savenode)
        if match and node and node["node"] == number:
            return match["password"]
    return None


def scan(directory: str | Path) -> Scan:
    """Everything the preview says. Never raises."""
    root = Path(directory)
    files: list[FileReport] = []
    problems: list[str] = []
    try:
        if not root.is_dir():
            return Scan(str(directory), [], [], [f"{directory} isn't a folder."])
        texts: dict[str, Optional[str]] = {}
        for name in (RPT, *OPTIONAL_FILES):
            texts[name], report = read_conf(root, name)
            if not (name != RPT and report.status == "missing"):
                files.append(report)
        rpt = texts[RPT]
        if rpt is None:
            report = files[0]
            if report.status == "missing":
                problems.append(f"rpt.conf isn't in {directory}.")
            else:
                problems.append(f"rpt.conf couldn't be read: {report.detail}.")
            return Scan(str(directory), files, [], problems)
        nodes = _nodes(rpt, texts)
        if not nodes:
            problems.append("rpt.conf has no node in it (a node is a section named by its number, such as [1999]).")
        return Scan(str(directory), files, nodes, problems)
    except Exception:  # a reader of other people's files must not take the dashboard down with it
        _logger.exception("reading the AllStar import folder failed")
        return Scan(str(directory), files, [], [*problems, "Something in those files couldn't be understood."])


def _nodes(rpt: str, texts: dict[str, Optional[str]]) -> list[SourceNode]:
    sections = parse_sections(rpt)
    extensions = texts.get("extensions.conf") or ""
    hinted = _NODE_VAR.search(extensions)
    result: list[SourceNode] = []
    seen: set[str] = set()
    for section in sections:
        if not _DIGITS.match(section.name):
            continue
        number = section.name
        if number in seen:
            continue
        seen.add(number)
        notes: list[str] = []
        try:
            callsign, note = _callsign(sections, number)
            if note:
                notes.append(note)
            macro = (effective(sections, number, "startup_macro") or "").strip()
            if macro and not _MACRO.match(macro):
                notes.append(f"Its startup macro ({macro[:20]}) isn't a plain DTMF string, so it isn't carried.")
                macro = ""
            links, skipped = _link_entries(sections, number)
            if skipped:
                notes.append(f"{skipped} link entries point at this machine itself and are left out.")
            password = password_for(texts, number)
            usable = bool(password and _PASSWORD_OK.match(password))
            if password and not usable:
                notes.append("Its registration password has characters or a length the dashboard can't write (6 or more letters, digits, _ or -). Enter it under Edit node afterwards.")
            if not password and int(number) >= 2000:
                notes.append("No registration was found, so the node won't register until you add the password under Edit node.")
            result.append(SourceNode(
                number=number,
                callsign=callsign,
                startup_macro=macro,
                links=links,
                links_skipped=skipped,
                has_registration=bool(password),
                password_usable=usable,
                radio=(effective(sections, number, "rxchannel") or "").strip(),
                notes=notes,
            ))
        except Exception:  # one odd stanza leaves that node out, not the others
            _logger.exception("couldn't read node %s from the import folder", number)
    _suggest(result, hinted["node"] if hinted else None)
    return result


def _suggest(nodes: list[SourceNode], hinted: Optional[str]) -> None:
    """Mark the one most likely wanted: the node extensions.conf names, then one that registers, then a real radio."""
    if not nodes:
        return
    chosen = next((n for n in nodes if n.number == hinted), None)
    chosen = chosen or next((n for n in nodes if n.has_registration), None)
    chosen = chosen or next((n for n in nodes if n.radio and not n.radio.lower().startswith("local/")), None)
    (chosen or nodes[0]).suggested = True


def read_password(directory: str | Path, number: str) -> Optional[str]:
    """The registration password for the apply step, read again from the files. Not logged, not returned to a client."""
    root = Path(directory)
    texts: dict[str, Optional[str]] = {}
    for name in ("rpt_http_registrations.conf", "iax.conf", "savenode.conf"):
        texts[name], _ = read_conf(root, name)
    password = password_for(texts, number)
    return password if password and _PASSWORD_OK.match(password) else None


def source_node(directory: str | Path, number: str) -> Optional[SourceNode]:
    return next((n for n in scan(directory).nodes if n.number == number), None)


def is_placeholder(number: str) -> bool:
    return number in PLACEHOLDER_NODES


def directory_from_env(env) -> Optional[str]:
    value = env.get("MOREOPENREPEATER_ALLSTAR_IMPORT_DIR", "").strip()
    return value or None
