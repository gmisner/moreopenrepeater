"""Keep the API/core compatibility seam aligned with its in-process adapter."""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from api.service import RepeaterService
from api.service.core_handle import CoreHandle


ROOT = Path(__file__).resolve().parents[2]
API_ROOT = ROOT / "src" / "api"


def _declared_members() -> set[str]:
    return {
        name
        for name in set(CoreHandle.__annotations__) | set(dir(CoreHandle))
        if not name.startswith("_")
    }


def test_repeater_service_provides_every_core_handle_member() -> None:
    service = RepeaterService()
    missing = sorted(name for name in _declared_members() if not hasattr(service, name))
    assert not missing, f"RepeaterService is missing CoreHandle members: {', '.join(missing)}"


def test_api_service_attributes_are_declared_by_core_handle() -> None:
    declared = _declared_members()
    used: dict[str, set[str]] = {}
    for path in API_ROOT.rglob("*.py"):
        if path.parent == API_ROOT / "service" or API_ROOT / "service" in path.parents:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        names: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute):
                continue
            if isinstance(node.value, ast.Name) and node.value.id in {"service", "svc", "_service"}:
                names.add(node.attr)
            elif (
                isinstance(node.value, ast.Attribute)
                and isinstance(node.value.value, ast.Name)
                and node.value.value.id == "self"
                and node.value.attr == "_service"
            ):
                names.add(node.attr)
        if names:
            used[str(path.relative_to(ROOT))] = names - declared
    drift = [f"{path}: {', '.join(sorted(names))}" for path, names in used.items() if names]
    assert not drift, "API uses members missing from CoreHandle:\n" + "\n".join(sorted(drift))


def test_stub_can_be_passed_as_a_core_handle() -> None:
    members = _declared_members()

    class Stub:
        def __init__(self) -> None:
            for name in members:
                value: Any = (lambda *args, **kwargs: None) if callable(getattr(CoreHandle, name, None)) else None
                setattr(self, name, value)

    def accepts_core(handle: CoreHandle) -> CoreHandle:
        return handle

    stub = Stub()
    assert isinstance(stub, CoreHandle)
    assert accepts_core(stub) is stub
