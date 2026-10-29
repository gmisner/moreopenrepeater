"""Sign-in session: who am I, login, logout."""
from __future__ import annotations

import asyncio
import logging
from typing import Callable, Optional

from fastapi import FastAPI, HTTPException, Request, Response

from ..auth import SESSION_COOKIE_NAME, SESSION_TTL_SECONDS, SessionStore
from ..models import LoginRequest, SessionResponse

_auth_logger = logging.getLogger("moreopenrepeater.auth")


def register(
    app: FastAPI,
    *,
    identify: Callable,
    auth_enabled: Callable[[], bool],
    check_login: Callable[[str, str], Optional[object]],
    sessions: SessionStore,
    session_response: Callable[[Optional[object]], SessionResponse],
    local_identity: object,
    login_failure_delay: float,
) -> None:
    @app.get("/api/session", response_model=SessionResponse)
    def get_session(request: Request) -> SessionResponse:
        return session_response(identify(request))

    @app.post("/api/login", response_model=SessionResponse)
    async def login(body: LoginRequest, request: Request, response: Response) -> SessionResponse:
        if not auth_enabled():
            return session_response(local_identity)
        request.state.audit_actor = body.username
        # scrypt takes tens of milliseconds; keep it off the event loop.
        identity = await asyncio.get_running_loop().run_in_executor(None, check_login, body.username, body.password)
        if identity is None:
            _auth_logger.warning("failed login for %r from %s", body.username, request.client.host if request.client else "?")
            await asyncio.sleep(login_failure_delay)
            raise HTTPException(status_code=401, detail="Invalid username or password")
        sessions.revoke(request.cookies.get(SESSION_COOKIE_NAME))
        response.set_cookie(
            SESSION_COOKIE_NAME,
            sessions.create(body.username),
            max_age=SESSION_TTL_SECONDS,
            httponly=True,
            samesite="strict",
            secure=request.url.scheme == "https",
        )
        _auth_logger.info("login succeeded for %r", body.username)
        return session_response(identity)

    @app.post("/api/logout", response_model=SessionResponse)
    async def logout(request: Request, response: Response) -> SessionResponse:
        sessions.revoke(request.cookies.get(SESSION_COOKIE_NAME))
        response.delete_cookie(SESSION_COOKIE_NAME, httponly=True, samesite="strict")
        return session_response(None if auth_enabled() else local_identity)
