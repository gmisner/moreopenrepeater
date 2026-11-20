"""API models for importing an existing AllStarLink node (api.allstar_import).

No model has a password in it, in either direction: the registration password is read from the source files when
the import is applied and goes straight to the writers."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from .linking import AllStarStatusResponse


class AllStarImportFile(BaseModel):
    name: str
    status: Literal["read", "missing", "unreadable"]
    detail: str = ""
    included: list[str] = []


class AllStarImportNode(BaseModel):
    number: str
    callsign: str
    allowed_links: int
    startup_macro: str
    has_registration: bool
    password_usable: bool
    radio: str  # shown as "not copied"
    notes: list[str]
    suggested: bool


class AllStarImportTarget(BaseModel):
    state: Literal["new", "same", "replace", "blocked"]
    existing: Optional[str] = None
    reason: Optional[str] = None


class AllStarImportPreview(BaseModel):
    configured: bool  # a folder was given to import from
    available: bool  # the container node's files can be edited from here
    found: bool
    source: Optional[str]
    files: list[AllStarImportFile]
    nodes: list[AllStarImportNode]
    problems: list[str]
    will_take: list[str]
    will_not_take: list[str]
    target: Optional[dict[str, AllStarImportTarget]]  # by source node number


class AllStarImportRequest(BaseModel):
    node: str = Field(pattern=r"^[0-9]{1,10}$")
    replace: bool = False
    callsign: str = Field(default="", max_length=12, pattern=r"^[A-Za-z0-9/-]*$")


class AllStarImportResult(BaseModel):
    status: AllStarStatusResponse
    took: list[str]
    left: list[str]
    snapshot: Optional[str]
