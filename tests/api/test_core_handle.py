"""Keep the API/core compatibility seam aligned with its in-process adapter."""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from api.service import RepeaterService
from api.service.core_handle import ApiCoreHandle, CoreHandle


ROOT = Path(__file__).resolve().parents[2]
API_ROOT = ROOT / "src" / "api"


def _declared_members(protocol: type = CoreHandle) -> set[str]:
    """Every member a protocol declares, including the ones it inherits."""
    names: set[str] = set(dir(protocol))
    for klass in protocol.__mro__:
        names |= set(getattr(klass, "__annotations__", {}))
    return {name for name in names if not name.startswith("_")}


def test_repeater_service_provides_every_core_handle_member() -> None:
    service = RepeaterService()
    missing = sorted(name for name in _declared_members() if not hasattr(service, name))
    assert not missing, f"RepeaterService is missing CoreHandle members: {', '.join(missing)}"


def test_remote_core_provides_every_api_core_handle_member(tmp_path) -> None:
    """The API runs against a RemoteCore when the core is a separate process, so a member the API's protocol
    declares that it lacks is an AttributeError waiting for the first time something asks: the scheduled
    weather announcement did, every hour, for as long as the API has been split."""
    from api.remote_core import RemoteCore

    remote = RemoteCore(tmp_path / "core.sock")

    def present(name: str) -> bool:
        try:
            getattr(remote, name)
        except AttributeError:
            return False
        except Exception:  # noqa: BLE001 - a property that says the core is not reached yet is still there
            return True
        return True

    missing = sorted(name for name in _declared_members(ApiCoreHandle) if not present(name))
    assert not missing, f"RemoteCore is missing ApiCoreHandle members: {', '.join(missing)}"


def test_everything_the_api_composition_reads_from_the_service_exists_on_remote_core(tmp_path) -> None:
    """`runtime.py` is the part of the API that runs when the core is another process, so every attribute it
    reads from `service` has to exist on `RemoteCore`, not merely on the in-process protocol. The hardware
    modules are excluded: they run inside the core."""
    from api.remote_core import RemoteCore

    remote = RemoteCore(tmp_path / "core.sock")
    tree = ast.parse((API_ROOT / "runtime.py").read_text(encoding="utf-8"))
    used = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "service"
    }

    def present(name: str) -> bool:
        try:
            getattr(remote, name)
        except AttributeError:
            return False
        except Exception:  # noqa: BLE001 - a property that says the core is not reached yet is still there
            return True
        return True

    # Only read by `node_link_loop`, which is not started when the core is a separate process: AllStar
    # linking has no split implementation yet, and the API says so in its log instead of failing.
    not_in_split_yet = {"handle_link_event", "set_link_command_sink"}
    missing = sorted(name for name in used - not_in_split_yet if not present(name))
    assert not missing, f"runtime.py reads these from the service but RemoteCore has no such attribute: {', '.join(missing)}"


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


# Files that belong in the API process (Stage 3 slice 7c-1). They may use only what
# `ApiCoreHandle` declares, which is what a core in its own process can serve to a
# remote API. A core-side member here would block that split.
API_SIDE_FILES = [
    *sorted(str(p.relative_to(API_ROOT)) for p in (API_ROOT / "routes").glob("*.py")),
    "app.py", "backup.py", "support_bundle.py", "metrics.py", "links.py", "net.py", "airport_weather.py",
    "site_sensors.py", "homeassistant.py", "transcripts.py",
]


def _service_attributes(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
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
    return names


def test_api_side_files_use_only_what_the_api_protocol_declares() -> None:
    api_members = _declared_members(ApiCoreHandle)
    drift = {}
    for name in API_SIDE_FILES:
        extra = _service_attributes(API_ROOT / name) - api_members
        if extra:
            drift[name] = sorted(extra)
    assert not drift, f"API-side files use core-side members: {drift}"


def test_api_side_files_are_annotated_with_the_api_protocol_not_the_full_one() -> None:
    wrong = [
        name for name in API_SIDE_FILES
        if "CoreHandle" in (API_ROOT / name).read_text() and "ApiCoreHandle" not in (API_ROOT / name).read_text()
    ]
    assert not wrong, f"these still annotate with the full CoreHandle: {wrong}"


def test_the_full_handle_extends_the_api_handle_and_the_service_satisfies_both() -> None:
    assert ApiCoreHandle in CoreHandle.__mro__
    service = RepeaterService()
    for protocol in (ApiCoreHandle, CoreHandle):
        missing = sorted(n for n in _declared_members(protocol) if not hasattr(service, n))
        assert not missing, f"RepeaterService is missing {protocol.__name__} members: {missing}"
    assert isinstance(service, ApiCoreHandle) and isinstance(service, CoreHandle)


def test_the_api_protocol_is_the_smaller_one_and_has_no_core_side_only_members() -> None:
    api, full = _declared_members(ApiCoreHandle), _declared_members(CoreHandle)
    assert api < full  # a strict subset
    core_side_only = {"tick", "begin_patch", "end_patch", "handle_audio_events", "handle_link_event", "audio_output",
                      "renderer", "ptt_active", "cos_active", "add_config_listener", "queue_announcement", "controller"}
    assert not (core_side_only & api), sorted(core_side_only & api)
    assert core_side_only <= full


def test_the_lockout_route_no_longer_reaches_through_the_service_to_the_controller() -> None:
    assert "service.controller" not in (API_ROOT / "routes" / "core.py").read_text()
    service = RepeaterService()
    assert service.locked_out is False
