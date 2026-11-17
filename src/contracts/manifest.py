"""What a module declares about itself: capabilities, devices and resources.

A module ships a manifest and announces it at startup. Compose overrides and
the dashboard's device-ownership view are derived from it (ADR 0005 item 6).
"""
from __future__ import annotations

import re
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .common import CODE_PATTERN, MODULE_PATTERN, Message
from .versioning import is_compatible, parse_version


class DeviceAccess(str, Enum):
    READ = "read"
    WRITE = "write"
    READ_WRITE = "read_write"


class SafeState(str, Enum):
    """What the module does when it loses the device."""

    NONE = "none"  # nothing to do
    DISABLE_MODULE = "disable_module"  # stop and report failed
    UNKEY_TRANSMITTER = "unkey_transmitter"  # make sure the transmitter is off
    PUBLISH_STALE = "publish_stale"  # keep publishing, marked stale


class DeviceNeed(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    #: Logical name used in this manifest (`gps_serial`).
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    #: A stable identity, never `/dev/ttyUSB0`: a udev symlink such as
    #: `/dev/serial/by-id/...`, or a bus address such as `i2c:1:0x52`.
    match: str = Field(min_length=1, max_length=200)
    access: DeviceAccess
    #: Only one module may hold an exclusive device. Shared buses are owned per
    #: address, so two modules can use different addresses on one bus.
    exclusive: bool = True
    required: bool = True
    safe_state_on_loss: SafeState = SafeState.DISABLE_MODULE

    @field_validator("match")
    @classmethod
    def _stable_identity(cls, value: str) -> str:
        if "/dev/ttyUSB" in value or "/dev/ttyACM" in value or "/dev/video" in value or ".." in value:
            raise ValueError("use a stable udev name or bus address, not a numbered device node")
        return value


class ResourceBudget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    memory_mb: int = Field(gt=0, le=4096)
    cpu_millicores: int = Field(gt=0, le=4000)


class ModuleManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=MODULE_PATTERN)
    version: str = Field(min_length=1, max_length=32)
    #: The contract set this module was built for, as `major.minor`.
    contract_version: str
    capabilities: list[str] = Field(default_factory=list, max_length=50)
    devices: list[DeviceNeed] = Field(default_factory=list, max_length=20)
    resources: ResourceBudget
    #: Port serving `/health` and `/metrics` inside the container.
    health_port: int = Field(ge=1024, le=65535)

    @field_validator("contract_version")
    @classmethod
    def _parses(cls, value: str) -> str:
        parse_version(value)
        return value

    @field_validator("capabilities")
    @classmethod
    def _capability_names(cls, value: list[str]) -> list[str]:
        bad = [c for c in value if not re.match(CODE_PATTERN, c)]
        if bad:
            raise ValueError(f"capabilities must be dotted lowercase names, got {bad[0]!r}")
        return value

    @model_validator(mode="after")
    def _no_duplicate_devices(self) -> "ModuleManifest":
        names = [d.name for d in self.devices]
        if len(set(names)) != len(names):
            raise ValueError("a device name appears twice")
        return self

    def works_with_core(self, core: tuple[int, int]) -> bool:
        return is_compatible(parse_version(self.contract_version), core)


class ModuleAnnounce(Message):
    """Sent by a module at startup: here is who I am and what I need."""

    kind: Literal["module.announce"] = "module.announce"
    manifest: ModuleManifest


def find_device_conflicts(manifests: list[ModuleManifest]) -> list[str]:
    """One owner per device. Returns one sentence per conflict (empty when fine).

    Two modules that want the same `match` conflict when either needs it
    exclusively. Two shared (non-exclusive) readers do not.
    """
    claims: dict[str, list[tuple[str, DeviceNeed]]] = {}
    for manifest in manifests:
        for device in manifest.devices:
            claims.setdefault(device.match, []).append((manifest.id, device))
    conflicts = []
    for match, owners in sorted(claims.items()):
        if len(owners) > 1 and any(device.exclusive for _, device in owners):
            names = ", ".join(sorted(module for module, _ in owners))
            conflicts.append(f"{match} is wanted by {names}, and at least one needs it exclusively")
    return conflicts
