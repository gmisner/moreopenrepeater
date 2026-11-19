"""Search the structured event history kept by the `logd` collector (#221, #229)."""
from __future__ import annotations

from typing import Optional, Sequence

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse

from ..log_history import LogHistory, LogHistoryRefused, LogHistoryUnavailable


def register(app: FastAPI, *, log_history: LogHistory, auth_dependencies: Sequence) -> None:
    @app.exception_handler(LogHistoryUnavailable)
    async def history_is_unavailable(request: Request, error: LogHistoryUnavailable) -> JSONResponse:
        code = "logd.unavailable" if error.configured else "logd.not_configured"
        return JSONResponse(status_code=503, content={"detail": str(error), "code": code})

    @app.exception_handler(LogHistoryRefused)
    async def history_refused(request: Request, error: LogHistoryRefused) -> JSONResponse:
        return JSONResponse(status_code=error.status, content={"detail": str(error), "code": "logd.refused"})

    @app.get("/api/log-history/status", dependencies=auth_dependencies)
    def history_status() -> dict:
        """Whether there is a history to search, for the page to decide what to show. Never an error."""
        return log_history.status()

    @app.get("/api/log-history/events", dependencies=auth_dependencies)
    def history_events(
        q: Optional[str] = Query(default=None, max_length=200),
        min_severity: Optional[str] = Query(default=None, max_length=10),
        module: Optional[str] = Query(default=None, max_length=64),
        event: Optional[str] = Query(default=None, max_length=80),
        correlation_id: Optional[str] = Query(default=None, max_length=64),
        since: Optional[str] = Query(default=None, max_length=40),
        until: Optional[str] = Query(default=None, max_length=40),
        limit: int = Query(default=100, ge=1, le=500),
        before: Optional[int] = Query(default=None, ge=0),
    ) -> dict:
        """Events newest first. `before` is the `next_before` of the previous page."""
        return log_history.events(
            q=q, min_severity=min_severity, module=module, event=event, correlation_id=correlation_id,
            since=since, until=until, limit=limit, before=before,
        )

    @app.get("/api/log-history/gaps", dependencies=auth_dependencies)
    def history_gaps(limit: int = Query(default=50, ge=1, le=200)) -> dict:
        """What the collector knows is missing from the record (restarts, dropped events, pruning)."""
        return log_history.gaps(limit=limit)
