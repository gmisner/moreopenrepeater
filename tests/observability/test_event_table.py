"""The events table in docs/docker.md lists every event name the code can log, and no name the code does not (#221).

Event names are string literals in `log_event(...)` calls (or in a small helper that passes its own argument on
to `log_event`), so they are read from the source rather than from a registry that could drift from it. The
facts a call passes must also survive the redaction filter: a fact called `pin` or `text` would be masked and
never searchable, which is easy to do by accident.
"""
import ast
import re
from pathlib import Path

from contracts.redaction import redact

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
DOCS = (ROOT / "docs" / "docker.md").read_text()
TABLE = DOCS[DOCS.index("### Events the core names"):DOCS.index("**One cycle, one id.**")]
CONTROL_ARGUMENTS = {"error_code", "correlation_id", "facts_in_text", "exc_info"}  # not facts: they steer log_event itself
EVENT_NAME = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")


def _calls(tree):
    return [node for node in ast.walk(tree) if isinstance(node, ast.Call)]


def _callee(node):
    return node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", None)


def _event_argument(call, position):
    if len(call.args) > position:
        return call.args[position]
    return next((kw.value for kw in call.keywords if kw.arg == "event"), None)


def _wrappers(trees):
    """Functions that take an event name as a parameter and hand it to `log_event`: name -> position of that parameter."""
    found = {}
    for tree in trees:
        for function in (n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))):
            parameters = [a.arg for a in function.args.args]
            for call in _calls(function):
                argument = _event_argument(call, 2) if _callee(call) == "log_event" else None
                if isinstance(argument, ast.Name) and argument.id in parameters:
                    found[function.name] = parameters.index(argument.id) - (1 if parameters[:1] == ["self"] else 0)
    return found


def scan():
    """(names, facts): every literal event name and every fact name passed to `log_event`, with where it was."""
    trees = {path: ast.parse(path.read_text()) for path in SRC.rglob("*.py") if "observability" not in path.parts}
    wrappers = _wrappers(trees.values())
    names, dynamic, facts = {}, set(), {}
    for path, tree in trees.items():
        for call in _calls(tree):
            callee = _callee(call)
            if callee == "log_event":
                argument = _event_argument(call, 2)
            elif callee in wrappers:
                argument = _event_argument(call, wrappers[callee])
            else:
                continue
            where = f"{path.relative_to(SRC)}:{call.lineno}"
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                names[argument.value] = where
            elif isinstance(argument, ast.JoinedStr):  # f"{area}.loop_failed"
                dynamic.add("".join(v.value if isinstance(v, ast.Constant) else "<area>" for v in argument.values))
            if callee == "log_event":
                for keyword in call.keywords:
                    if keyword.arg and keyword.arg not in CONTROL_ARGUMENTS:
                        facts[keyword.arg] = where
    return names, dynamic, facts


def documented():
    names = set()
    for row in TABLE.splitlines():
        if not row.startswith("| `"):
            continue
        first_column = row.split("|")[1]
        previous = None
        for token in re.findall(r"`([^`]+)`", first_column):
            if token.startswith("."):
                if previous is None:
                    continue
                token = previous.split(".")[0] + token  # `.call_connected` after `autopatch.call_started`
            if EVENT_NAME.match(token):
                names.add(token)
                previous = token
    return names


def test_every_event_the_code_names_is_in_the_table():
    names, dynamic, _facts = scan()
    assert len(names) > 60  # the scan found the calls: this is not passing because it saw nothing
    missing = sorted(f"{name} ({where})" for name, where in names.items() if name not in documented())
    assert not missing, "add these to the events table in docs/docker.md: " + ", ".join(missing)
    assert dynamic == {"<area>.loop_failed"} and "`<area>.loop_failed`" in TABLE


def test_the_table_names_no_event_the_code_does_not_log():
    names, _dynamic, _facts = scan()
    stale = sorted(documented() - set(names))
    assert not stale, "these are in the events table but nothing logs them: " + ", ".join(stale)


def test_no_fact_is_one_the_redaction_filter_would_mask():
    _names, _dynamic, facts = scan()
    assert "pin" not in facts and "gpio" in facts  # the case this test exists for: a `pin` fact comes out as [redacted]
    masked = sorted(f"{fact} ({where})" for fact, where in facts.items() if redact({fact: 1}) != {fact: 1} or redact({fact: "x"}) != {fact: "x"})
    assert not masked, "these facts would be masked and never searchable, rename them: " + ", ".join(masked)
