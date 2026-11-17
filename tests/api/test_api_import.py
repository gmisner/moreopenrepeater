"""Importing part of the API package must not build the app (inventory finding F3).

`api.app` creates the FastAPI app, opens the audio device and loads state when
it is imported. Anything that only needs the service or a helper module (a
future core-only process, a script, a test) must be able to import it without
that.
"""
import importlib
import subprocess
import sys
from pathlib import Path

import pytest

SRC = str(Path(__file__).resolve().parents[2] / "src")

# What importing the module must not drag in.
FORBIDDEN = ("fastapi", "starlette", "api.app", "api.runtime")


def _loaded_after_import(module: str) -> dict[str, bool]:
    code = (
        "import sys, json; sys.path.insert(0, sys.argv[1]);"
        f"import {module};"
        f"print(json.dumps({{name: name in sys.modules for name in {FORBIDDEN!r}}}))"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", code, SRC], capture_output=True, text=True, timeout=60, check=True
    )
    import json

    return json.loads(result.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("module", ["api.service", "api.boards"])
def test_importing_a_submodule_does_not_build_the_app(module):
    assert _loaded_after_import(module) == {name: False for name in FORBIDDEN}


def test_controller_does_not_import_the_web_stack():
    assert _loaded_after_import("controller.state_machine") == {name: False for name in FORBIDDEN}


def test_package_exports_still_resolve():
    import api

    assert set(api.__all__) == {"app", "create_app", "CoreHandle", "RepeaterService", "StatusSnapshot"}
    app_module = importlib.import_module("api.app")
    service_module = importlib.import_module("api.service")
    assert api.create_app is app_module.create_app
    assert api.RepeaterService is service_module.RepeaterService
    assert api.StatusSnapshot is service_module.StatusSnapshot
    assert api.CoreHandle is service_module.CoreHandle
    assert api.app is app_module.app  # `uvicorn api:app` keeps working


def test_unknown_package_attribute_still_raises():
    import api

    with pytest.raises(AttributeError):
        api.no_such_thing  # noqa: B018
