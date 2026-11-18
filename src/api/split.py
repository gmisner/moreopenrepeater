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

import inspect
from typing import Any, Optional

from .service.core_handle import CommandRefused, CoreError


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
    is_stub = True  # a stand-in with nothing behind it; `CoreHardware` is the one with a status

    def __init__(self, name: str) -> None:
        self._name = name

    def __getattr__(self, attr: str) -> _Refusal:
        if attr.startswith("__"):
            raise AttributeError(attr)
        return _Refusal(self._name, attr)

    def __repr__(self) -> str:
        return f"<{self._name}: runs in the core>"


class CoreHardware(Unavailable):
    """The API's view of one of the core's hardware parts: its status, as the core last reported it.

    `status()` answers from what the core reports every couple of seconds (and raises `CoreUnavailable`
    if the core is not being heard from, or did not report that part). Everything else (turning an
    output on, dialling, saving a trunk) still refuses with `NotInSplitMode`: those are commands, and
    the core does not take them over the socket yet. `asynchronous` is for the parts whose status
    the dashboard awaits."""

    def __init__(self, name: str, remote, *, asynchronous: bool = False, monitor=None) -> None:
        super().__init__(name)
        self._remote = remote
        self.is_stub = False
        if monitor is not None:
            self.monitor = monitor  # what listeners subscribe to: the core's audio, over the socket
        if asynchronous:
            self.status = self._status_async  # type: ignore[assignment]

    def status(self) -> dict:  # type: ignore[override]
        return dict(self._remote.hardware_status(self._name))

    async def _status_async(self) -> dict:
        return dict(self._remote.hardware_status(self._name))

    # -- the few things the core takes as commands (each returns a coroutine; see `settled`) ----------

    def _only(self, part: str, what: str) -> None:
        if self._name != part:
            raise NotInSplitMode(f"{self._name}.{what} needs the core's hardware, which runs in another process")

    def name(self, pin: int) -> str:
        """A GPIO output's name, from the pins the core reports."""
        self._only("gpio", "name")
        for entry in self._remote.hardware_status("gpio").get("pins", []):
            if entry.get("pin") == pin and entry.get("name"):
                return str(entry["name"])
        return f"Output {pin}"

    def set_output(self, pin: int, on: bool):
        self._only("gpio", "set_output")
        return self._remote.command("gpio_set_output", pin=pin, on=on)

    def retune(self):
        self._only("remote_base", "retune")
        return self._remote.command("remote_base_retune")

    async def dial(self, number: str, actor: str) -> Optional[str]:
        """Start a call; returns why not, as the in-process one does. The core decides."""
        self._only("autopatch", "dial")
        try:
            await self._remote.command("autopatch_dial", number=number, actor=actor[:200] or "local")
        except CommandRefused as refused:
            if refused.code == "command.hardware_refused":
                return refused.message
            raise
        return None

    def hangup(self, reason: str):
        self._only("autopatch", "hangup")
        return self._remote.command("autopatch_hangup", reason=reason[:200] or "hung up")


def safe_status(part: Any) -> dict:
    """A part's status, or {} if it cannot be had now (the core is away). For places that report
    on everything and must not fail because one part is unknown: metrics, the support bundle."""
    try:
        return dict(part.status())
    except CoreError:
        return {}


async def settled(value: Any) -> Any:
    """What a hardware action returned, once it is done. In process it is already the result; apart
    from the core it is a coroutine that sends the command and waits for the core's answer."""
    return await value if inspect.isawaitable(value) else value
