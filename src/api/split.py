"""Stand-ins for the hardware the core owns, for an API that runs apart from it
(Stage 3 slice 7c-7, #229).

With the core in its own process, the audio engine, PTT, GPIO, fan, link radio, remote base,
autopatch and the AllStar and DMR audio belong to the core and this process holds no device. The
dashboard still has pages that talk about them. `Unavailable` is what those pages find instead: it
never touches a device, and anything that tries to use it raises `NotInSplitMode`, which the app
answers with a 503 that says so. Reading its simple fields gives empty values so a page that lists
things shows nothing, not an error.
"""
from __future__ import annotations

from typing import Any

from .service.core_handle import CoreError


class NotInSplitMode(CoreError):
    """This needs hardware that belongs to the core, which runs in another process."""


class _Refusal:
    """What an attribute of `Unavailable` is: calling it refuses, and it is empty otherwise."""

    def __init__(self, owner: str, attr: str) -> None:
        self._what = f"{owner}.{attr}"

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        raise NotInSplitMode(f"{self._what} needs the core's hardware, which runs in another process")

    def __bool__(self) -> bool:
        return False

    def __iter__(self):
        return iter(())

    def __len__(self) -> int:
        return 0

    def __reversed__(self):
        return iter(())

    def __getattr__(self, attr: str) -> "_Refusal":
        if attr.startswith("__"):
            raise AttributeError(attr)
        return _Refusal(self._what, attr)


class Unavailable:
    """Stands in for one of the core's hardware collaborators."""

    #: Simple fields the composition reads; the rest refuse when used.
    cm108 = None
    node = None
    directory = None

    def __init__(self, name: str) -> None:
        self._name = name

    def __getattr__(self, attr: str) -> _Refusal:
        if attr.startswith("__"):
            raise AttributeError(attr)
        return _Refusal(self._name, attr)

    def __repr__(self) -> str:
        return f"<{self._name}: runs in the core>"
