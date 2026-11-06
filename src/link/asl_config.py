"""Edits to AllStarLink's rpt.conf and modules.conf, as text.

AMI's UpdateConfig can't be used on rpt.conf: Asterisk saves a file by
writing out what it loaded, so every node and section that inherits from a
template (`[1999](node-main)`) gets the template's settings copied into it,
and the files rpt.conf includes are rewritten the same way. These edits
change only the lines they need to and leave the rest byte for byte.

Every line added ends with MARK, and every line replaced is kept as a
comment starting with SAVED, so `release_node` can put the stanza back the
way it was.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

MARK = "; set by moreopenrepeater"
SAVED = ";moreopenrepeater was: "

# What the controller needs from its node; see docs/allstar.md.
NODE_SETTINGS = ("rxchannel", "duplex", "linktolink", "hangtime", "althangtime", "nounkeyct")

_HEADER = re.compile(r"^\s*\[(?P<name>[^\]]+)\](?:\((?P<templates>[^)]*)\))?")
_SETTING = re.compile(r"^\s*(?P<key>[A-Za-z0-9_.,:\-]+)\s*=>?\s*(?P<value>.*)$")
_USRP = re.compile(r"^USRP/(?P<host>[^:]+):(?P<port>\d+):(?P<node_port>\d+)$", re.IGNORECASE)


def _strip_comment(value: str) -> str:
    out = []
    escaped = False
    for char in value:
        if escaped:
            out.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
            out.append(char)
        elif char == ";":
            break
        else:
            out.append(char)
    return "".join(out).strip()


@dataclass
class Section:
    name: str
    templates: list[str]
    start: int  # index of the header line
    end: int  # index after the last line
    settings: dict[str, str] = field(default_factory=dict)


def parse_sections(text: str) -> list[Section]:
    lines = text.splitlines()
    sections: list[Section] = []
    for index, line in enumerate(lines):
        header = _HEADER.match(line)
        if header:
            if sections:
                sections[-1].end = index
            templates = [t.strip() for t in (header["templates"] or "").split(",") if t.strip() and t.strip() not in ("!", "+")]
            sections.append(Section(header["name"].strip(), templates, index, len(lines)))
        elif line.lstrip().startswith("#"):
            if sections:
                sections[-1].end = index
                sections.append(Section("", [], index, len(lines)))  # an #include ends the section before it
        elif sections and not line.lstrip().startswith(";"):
            setting = _SETTING.match(line)
            if setting:
                sections[-1].settings[setting["key"].lower()] = _strip_comment(setting["value"])
    return [s for s in sections if s.name]


def _effective(sections: list[Section], name: str, key: str, depth: int = 0) -> Optional[str]:
    section = next((s for s in sections if s.name == name), None)
    if section is None or depth > 10:
        return None
    if key in section.settings:
        return section.settings[key]
    for template in reversed(section.templates):
        value = _effective(sections, template, key, depth + 1)
        if value is not None:
            return value
    return None


@dataclass(frozen=True)
class Node:
    number: str
    rxchannel: str
    duplex: str
    controlled: bool  # our settings are in its stanza

    def usrp(self) -> Optional[tuple[str, int, int]]:
        """(controller host, controller port, node port) when its radio is USRP."""
        match = _USRP.match(self.rxchannel)
        return (match["host"], int(match["port"]), int(match["node_port"])) if match else None


def nodes(text: str) -> list[Node]:
    sections = parse_sections(text)
    lines = text.splitlines()
    found = []
    for section in sections:
        if not section.name.isdigit():
            continue
        body = lines[section.start + 1 : section.end]
        found.append(Node(
            number=section.name,
            rxchannel=_effective(sections, section.name, "rxchannel") or "",
            duplex=_effective(sections, section.name, "duplex") or "",
            controlled=any(line.rstrip().endswith(MARK) for line in body),
        ))
    return found


def _section(text: str, name: str) -> Section:
    section = next((s for s in parse_sections(text) if s.name == name), None)
    if section is None:
        raise ValueError(f"no [{name}] section")
    return section


def section_settings(text: str, name: str) -> dict[str, str]:
    """A section's own settings (not its templates'), comments stripped."""
    return _section(text, name).settings


def _rejoin(lines: list[str], text: str) -> str:
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


def release_section(text: str, name: str) -> str:
    """Take our settings out of a section and put back what they replaced."""
    section = _section(text, name)
    lines = text.splitlines()
    body = []
    for line in lines[section.start + 1 : section.end]:
        if line.rstrip().endswith(MARK):
            continue
        body.append(line[len(SAVED):] if line.startswith(SAVED) else line)
    return _rejoin(lines[: section.start + 1] + body + lines[section.end :], text)


def control_section(text: str, name: str, settings: dict[str, str]) -> str:
    """Put `settings` at the top of a section, commenting out the lines they replace."""
    text = release_section(text, name)
    section = _section(text, name)
    lines = text.splitlines()
    body = []
    for line in lines[section.start + 1 : section.end]:
        setting = None if line.lstrip().startswith(";") else _SETTING.match(line)
        body.append(SAVED + line if setting and setting["key"].lower() in settings else line)
    ours = [f"{key} = {value}  {MARK}" for key, value in settings.items()]
    return _rejoin(lines[: section.start + 1] + ours + body + lines[section.end :], text)


def release_node(text: str, node: str) -> str:
    if not any(n.number == node for n in nodes(text)):
        raise ValueError(f"rpt.conf has no node {node}")
    return release_section(text, node)


def control_node(text: str, node: str, settings: dict[str, str]) -> str:
    if not any(n.number == node for n in nodes(text)):
        raise ValueError(f"rpt.conf has no node {node}")
    return control_section(text, node, settings)


def controller_settings(rxchannel: str) -> dict[str, str]:
    return {
        "rxchannel": rxchannel,
        "duplex": "0",  # app_rpt's mode for an external repeater controller
        "linktolink": "yes",  # full duplex: users can talk over a linked station
        "hangtime": "0",  # hang time and courtesy tone are the controller's
        "althangtime": "0",
        "nounkeyct": "1",
    }


# -- modules.conf --------------------------------------------------------------

_MODULE = re.compile(r"^(?P<indent>\s*)(?P<key>load|noload|require|preload|preload-require)(?P<sep>\s*=>?\s*)(?P<module>[A-Za-z0-9_.\-]+)(?P<rest>.*)$")


def module_state(text: str, module: str) -> Optional[str]:
    """`load`, `noload` (or `require`...) as the file has it, or None."""
    state = None
    for line in text.splitlines():
        match = _MODULE.match(line)
        if match and match["module"] == module:
            state = match["key"]
    return state


def set_module(text: str, module: str, load: bool) -> str:
    """Load (or not) `module` at startup, keeping the line's place and comment."""
    wanted = "load" if load else "noload"
    lines = text.splitlines()
    changed = False
    for index, line in enumerate(lines):
        match = _MODULE.match(line)
        if not match or match["module"] != module:
            continue
        loads = match["key"] != "noload"
        if loads == load:
            changed = True
            continue
        lines[index] = SAVED + line
        lines.insert(index, f"{match['indent']}{wanted}{match['sep']}{module}  {MARK}")
        changed = True
        break
    if not changed:
        section = next((s for s in parse_sections(text) if s.name == "modules"), None)
        at = section.end if section else len(lines)
        while at > 0 and not lines[at - 1].strip():
            at -= 1
        lines.insert(at, f"{wanted} = {module}  {MARK}")
    return _rejoin(lines, text)


def restore_module(text: str, module: str) -> str:
    """Undo `set_module`."""
    out = []
    for line in text.splitlines():
        match = _MODULE.match(line)
        if line.rstrip().endswith(MARK) and match and match["module"] == module:
            continue
        restored = line[len(SAVED):] if line.startswith(SAVED) else None
        restored_match = _MODULE.match(restored) if restored is not None else None
        out.append(restored if restored_match and restored_match["module"] == module else line)
    return _rejoin(out, text)


# -- node identity (number, callsign, HTTP registration) ----------------------

_REGISTER = re.compile(
    r"^(?P<prefix>\s*;*\s*register\s*=>\s*)(?P<node>[0-9]+):(?P<password>[^@\s]*)@(?P<rest>\S+)(?P<tail>.*)$",
    re.IGNORECASE,
)
_IDRECORDING = re.compile(r"^idrecording\s*=\s*(?P<value>.*)$", re.IGNORECASE)
_NODE_VAR = re.compile(r"^(?P<prefix>\s*NODE\s*=\s*)(?P<node>[0-9]+)(?P<tail>.*)$")
_SAVENODE_PASSWORD = re.compile(r"^(?P<prefix>\s*PASSWORD\s*=\s*).*$")
_SAVENODE_ENABLE = re.compile(r"^(?P<prefix>\s*ENABLE\s*=\s*).*$")
PUBLIC_NODE_MIN = 2000
REGISTER_HOST = "register.allstarlink.org"


def rename_node_stanza(text: str, old: str, new: str) -> str:
    """Rename a node section and node-keyed radio channels in rpt.conf."""
    if old == new:
        return text
    if not any(n.number == old for n in nodes(text)):
        raise ValueError(f"rpt.conf has no node {old}")
    if any(n.number == new for n in nodes(text)):
        raise ValueError(f"rpt.conf already has node {new}")
    lines = text.splitlines()
    out = []
    for line in lines:
        header = _HEADER.match(line)
        if header and header["name"].strip() == old:
            templates = f"({header['templates']})" if header["templates"] is not None else ""
            out.append(f"[{new}]{templates}")
            continue
        setting = None if line.lstrip().startswith(";") else _SETTING.match(line)
        if setting and setting["key"].lower() == "rxchannel":
            value = setting["value"]
            for prefix in ("SimpleUSB/", "Radio/", "Voter/", "USBRadio/"):
                if re.match(rf"^{re.escape(prefix)}{re.escape(old)}(\b|$)", value, re.IGNORECASE):
                    line = re.sub(
                        rf"^(\s*{re.escape(setting['key'])}\s*=>?\s*{re.escape(prefix)}){re.escape(old)}",
                        rf"\g<1>{new}",
                        line,
                        count=1,
                        flags=re.IGNORECASE,
                    )
                    break
        # Node map lines like `1999 = radio@127.0.0.1/...` in older configs.
        if re.match(rf"^\s*{re.escape(old)}\s*=", line):
            line = re.sub(rf"^(\s*){re.escape(old)}(\s*=)", rf"\g<1>{new}\2", line, count=1)
        out.append(line)
    return _rejoin(out, text)


def rename_section(text: str, old: str, new: str) -> str:
    """Rename `[old]` to `[new]` when present (simpleusb/usbradio/voter)."""
    if old == new or not text.strip():
        return text
    lines = text.splitlines()
    out = []
    changed = False
    for line in lines:
        header = _HEADER.match(line)
        if header and header["name"].strip() == old:
            templates = f"({header['templates']})" if header["templates"] is not None else ""
            out.append(f"[{new}]{templates}")
            changed = True
        else:
            out.append(line)
    return _rejoin(out, text) if changed else text


def set_idrecording(text: str, node: str, callsign: str) -> str:
    """Set `idrecording = |iCALLSIGN` in the node's stanza."""
    section = _section(text, node)
    lines = text.splitlines()
    body = []
    found = False
    value = f"|i{callsign}" if callsign else ""
    for line in lines[section.start + 1 : section.end]:
        if not line.lstrip().startswith(";") and _IDRECORDING.match(line):
            found = True
            if value:
                body.append(f"idrecording = {value}")
            continue
        body.append(line)
    if value and not found:
        body.insert(0, f"idrecording = {value}")
    return _rejoin(lines[: section.start + 1] + body + lines[section.end :], text)


def node_callsign(text: str, node: str) -> str:
    """Callsign from the node's `idrecording`, without the `|i` CW prefix."""
    try:
        value = _effective(parse_sections(text), node, "idrecording") or ""
    except ValueError:
        return ""
    value = value.strip()
    if value.startswith("|i"):
        value = value[2:]
    if value.upper() in ("", "NOTSET", "NONE"):
        return ""
    return value


def update_extensions_node(text: str, old: str, new: str) -> str:
    if old == new or not text.strip():
        return text
    lines = []
    for line in text.splitlines():
        match = _NODE_VAR.match(line)
        if match and match["node"] == old:
            lines.append(f"{match['prefix']}{new}{match['tail']}")
        else:
            lines.append(line)
    return _rejoin(lines, text)


def registration_password(text: str, node: str) -> Optional[str]:
    """Password on an active (uncommented) register line for `node`, if any."""
    for line in text.splitlines():
        if line.lstrip().startswith(";"):
            continue
        match = _REGISTER.match(line)
        if match and match["node"] == node:
            return match["password"] or None
    return None


def set_http_registration(text: str, node: str, password: str, old_node: Optional[str] = None) -> str:
    """Point HTTP registration at `node:password`. Private nodes are cleared."""
    if int(node) < PUBLIC_NODE_MIN:
        return remove_http_registration(text, old_node or node)
    lines = text.splitlines()
    target = old_node or node
    placeholders = {target, "1234", "1999", "1998"}
    out = []
    replaced = False
    for line in lines:
        match = _REGISTER.match(line)
        if match and match["node"] in placeholders | {node}:
            if replaced:
                # Drop extra placeholder/old lines so we don't stack registers.
                continue
            out.append(f"register => {node}:{password}@{REGISTER_HOST}")
            replaced = True
            continue
        out.append(line)
    if not replaced:
        insert_at = len(out)
        for index, line in enumerate(out):
            header = _HEADER.match(line)
            if header and header["name"].strip().lower() == "registrations":
                insert_at = index + 1
                break
        out.insert(insert_at, f"register => {node}:{password}@{REGISTER_HOST}")
    return _rejoin(out, text)


def remove_http_registration(text: str, node: str) -> str:
    if not text.strip():
        return text
    lines = [
        line
        for line in text.splitlines()
        if not ((match := _REGISTER.match(line)) and match["node"] == node and not line.lstrip().startswith(";"))
    ]
    return _rejoin(lines, text)


def set_iax_registration(text: str, node: str, password: str, old_node: Optional[str] = None) -> str:
    """Update commented or active IAX register lines the way asl-menu does."""
    if not text.strip():
        return text
    if int(node) < PUBLIC_NODE_MIN:
        return remove_iax_registration(text, old_node or node)
    lines = text.splitlines()
    target = old_node or node
    out = []
    replaced = False
    for line in lines:
        match = _REGISTER.match(line)
        if match and match["node"] in {target, node, "1998", "1999", "1234"}:
            prefix = match["prefix"]
            # Keep whatever leading `;` style the line had.
            out.append(f"{prefix}{node}:{password}@{match['rest']}{match['tail']}")
            replaced = True
            continue
        out.append(line)
    if not replaced:
        return text
    return _rejoin(out, text)


def remove_iax_registration(text: str, node: str) -> str:
    if not text.strip():
        return text
    lines = []
    for line in text.splitlines():
        match = _REGISTER.match(line)
        if match and match["node"] == node:
            continue
        lines.append(line)
    return _rejoin(lines, text)


def update_savenode(text: str, old: str, new: str, password: Optional[str] = None) -> str:
    if not text.strip():
        return text
    lines = text.splitlines()
    out = []
    for line in lines:
        match = _NODE_VAR.match(line)
        if match and match["node"] == old:
            out.append(f"{match['prefix']}{new}{match['tail']}")
            continue
        if password is not None and _SAVENODE_PASSWORD.match(line):
            out.append(f"{_SAVENODE_PASSWORD.match(line)['prefix']}{password}")
            continue
        if password is not None and _SAVENODE_ENABLE.match(line):
            out.append(f"{_SAVENODE_ENABLE.match(line)['prefix']}1")
            continue
        out.append(line)
    return _rejoin(out, text)
