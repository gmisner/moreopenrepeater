"""Prometheus scrape endpoint."""
from __future__ import annotations

import time
from typing import Optional

from fastapi import FastAPI, Response

from ..metrics import render_metrics
from ..service import RepeaterService


def register(
    app: FastAPI,
    *,
    service: RepeaterService,
    live_audio=None,
    activity_store=None,
    process_started_at: Optional[float] = None,
) -> None:
    started = process_started_at if process_started_at is not None else time.time()

    @app.get("/metrics")
    def prometheus_metrics() -> Response:
        body = render_metrics(
            service=service,
            live_audio=live_audio,
            activity_store=activity_store,
            process_started_at=started,
        )
        return Response(content=body, media_type="text/plain; version=0.0.4; charset=utf-8")
