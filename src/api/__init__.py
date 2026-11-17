"""The API package.

Importing a submodule (`api.service`, `api.boards`, ...) must not build the
app. `api.app` creates the FastAPI app, opens the audio device and loads state
when it is imported, so the package exports that need it load on first use
(PEP 562) and not at import time. Nothing changes for the service itself:
`moreopenrepeater-api` runs `api.app:main`, which imports `api.app` directly.
"""
from __future__ import annotations

import importlib
import sys
import types
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .app import app, create_app
    from .service import RepeaterService, StatusSnapshot

__all__ = ["app", "create_app", "RepeaterService", "StatusSnapshot"]

_EXPORTS = {
    "create_app": ".app",
    "RepeaterService": ".service",
    "StatusSnapshot": ".service",
}


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(target, __name__), name)
    globals()[name] = value
    return value


class _Package(types.ModuleType):
    """`api.app` is both a submodule and, for `from api import app` and
    `uvicorn api:app`, the FastAPI instance. Importing the submodule makes Python
    bind the module to that name on the package, which would silently turn the
    instance into a module. This keeps it pointing at the instance, in either
    import order."""

    @property
    def app(self) -> Any:
        return importlib.import_module(".app", __name__).app

    @app.setter
    def app(self, _submodule: Any) -> None:
        pass  # the import system binding the submodule; the property wins


sys.modules[__name__].__class__ = _Package
