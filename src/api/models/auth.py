"""Session, user, token, and agent-access API models."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from .types import Role

#: Far above what an account can have (a username up to 64, a password up to 200), so no real account is
#: locked out, and low enough that a login cannot be used to push a megabyte through scrypt or the log.
LOGIN_USERNAME_MAX = 256
LOGIN_PASSWORD_MAX = 1024


class LoginRequest(BaseModel):
    username: str = Field(max_length=LOGIN_USERNAME_MAX)
    password: str = Field(max_length=LOGIN_PASSWORD_MAX)


class SessionResponse(BaseModel):
    auth_required: bool
    authenticated: bool
    username: Optional[str]
    role: Optional[Role] = None


class UserResponse(BaseModel):
    username: str
    role: Role
    builtin: bool  # the env-configured admin; can't be edited here


class TokenResponse(BaseModel):
    id: str
    name: str
    username: str
    role: Optional[Role]  # None if the user is gone (the token no longer works)
    created_at: datetime
    last_used_at: Optional[datetime] = None


class AgentAccessResponse(BaseModel):
    transmit: bool  # API tokens may use endpoints that key the transmitter
    mcp_available: bool


class AgentAccessRequest(BaseModel):
    transmit: bool


class TokenCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    username: str = Field(min_length=1, max_length=64)


class TokenCreatedResponse(BaseModel):
    id: str
    token: str  # shown once; only a digest is kept
    tokens: list[TokenResponse]


class UserCreateRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._@-]+$")
    password: str = Field(min_length=8, max_length=200)
    role: Role


class UserUpdateRequest(BaseModel):
    password: Optional[str] = Field(default=None, min_length=8, max_length=200)
    role: Optional[Role] = None
