"""Users, API tokens, and agent transmit access (admin + person only)."""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Callable, Optional, Sequence

from fastapi import FastAPI, HTTPException, Request

from ..agents import AgentAccess, AgentEndpoint
from ..control_codes import ControlCodes
from ..models import (
    AgentAccessRequest,
    AgentAccessResponse,
    TokenCreatedResponse,
    TokenCreateRequest,
    TokenResponse,
    UserCreateRequest,
    UserResponse,
    UserUpdateRequest,
)
from ..tokens import TokenStore
from ..users import Role, UserError, UserStore
from ..auth import AuthSettings


def register(
    app: FastAPI,
    *,
    users: UserStore,
    tokens: TokenStore,
    agent_access: AgentAccess,
    agent_endpoint: AgentEndpoint,
    auth_settings: Optional[AuthSettings],
    control_codes: ControlCodes,
    role_of: Callable[[str], Optional[Role]],
    account_dependencies: Sequence,
) -> None:
    def list_users() -> list[UserResponse]:
        builtin = [UserResponse(username=auth_settings.username, role="admin", builtin=True)] if auth_settings else []
        return builtin + [UserResponse(username=u.username, role=u.role, builtin=False) for u in users.list()]

    @app.get("/api/users", response_model=list[UserResponse], dependencies=account_dependencies)
    def get_users() -> list[UserResponse]:
        return list_users()

    @app.post("/api/users", response_model=list[UserResponse], dependencies=account_dependencies)
    async def create_user(body: UserCreateRequest, request: Request) -> list[UserResponse]:
        request.state.audit_detail = f"{body.username} ({body.role})"
        try:
            await asyncio.get_running_loop().run_in_executor(None, users.add, body.username, body.password, body.role)
        except UserError as error:
            raise HTTPException(status_code=400, detail=str(error)) from None
        return list_users()

    @app.put("/api/users/{username}", response_model=list[UserResponse], dependencies=account_dependencies)
    async def update_user(username: str, body: UserUpdateRequest, request: Request) -> list[UserResponse]:
        changes = ([f"role → {body.role}"] if body.role else []) + (["password reset"] if body.password else [])
        request.state.audit_detail = f"{username}: {', '.join(changes)}"
        try:
            await asyncio.get_running_loop().run_in_executor(None, users.update, username, body.password, body.role)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"No user {username!r}") from None
        except UserError as error:
            raise HTTPException(status_code=400, detail=str(error)) from None
        return list_users()

    @app.delete("/api/users/{username}", response_model=list[UserResponse], dependencies=account_dependencies)
    def delete_user(username: str) -> list[UserResponse]:
        try:
            users.delete(username)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"No user {username!r}") from None
        except UserError as error:
            raise HTTPException(status_code=400, detail=str(error)) from None
        control_codes.remove(username)
        tokens.remove_user(username)
        return list_users()

    @app.get("/api/agent-access", response_model=AgentAccessResponse, dependencies=account_dependencies)
    def get_agent_access() -> AgentAccessResponse:
        return AgentAccessResponse(transmit=agent_access.transmit, mcp_available=agent_endpoint.available)

    @app.put("/api/agent-access", response_model=AgentAccessResponse, dependencies=account_dependencies)
    def put_agent_access(body: AgentAccessRequest, request: Request) -> AgentAccessResponse:
        request.state.audit_detail = "agents may transmit" if body.transmit else "agents may not transmit"
        agent_access.set_transmit(body.transmit)
        return get_agent_access()

    def token_list() -> list[TokenResponse]:
        return [
            TokenResponse(
                id=t.id,
                name=t.name,
                username=t.username,
                role=role_of(t.username),
                created_at=datetime.fromtimestamp(t.created_at),
                last_used_at=datetime.fromtimestamp(t.last_used_at) if t.last_used_at else None,
            )
            for t in tokens.list()
        ]

    @app.get("/api/tokens", response_model=list[TokenResponse], dependencies=account_dependencies)
    def list_tokens() -> list[TokenResponse]:
        return token_list()

    @app.post("/api/tokens", response_model=TokenCreatedResponse, dependencies=account_dependencies)
    def create_token(body: TokenCreateRequest, request: Request) -> TokenCreatedResponse:
        request.state.audit_detail = f"{body.name} for {body.username}"
        if role_of(body.username) is None:
            raise HTTPException(status_code=400, detail=f"No user {body.username!r}")
        if role_of(body.username) == "listener":
            raise HTTPException(status_code=400, detail="Listener accounts can't have API tokens")
        record, secret = tokens.create(body.name, body.username)
        return TokenCreatedResponse(id=record.id, token=secret, tokens=token_list())

    @app.delete("/api/tokens/{token_id}", response_model=list[TokenResponse], dependencies=account_dependencies)
    def revoke_token(token_id: str, request: Request) -> list[TokenResponse]:
        try:
            record = tokens.revoke(token_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="No such token") from None
        request.state.audit_detail = f"{record.name} ({record.username})"
        return token_list()
