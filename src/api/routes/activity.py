"""Activity summary, recent transmissions, and settings snapshot import/export."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Callable, Sequence

from fastapi import FastAPI, Query

from ..activity import RETENTION_DAYS, ActivityStore, summarize
from ..models import (
    ActivitySummaryResponse,
    SnapshotImportRequest,
    SnapshotModel,
    TransmissionResponse,
)
from ..service import RepeaterService


def register(
    app: FastAPI,
    *,
    service: RepeaterService,
    activity_store: ActivityStore,
    snapshot_to_import: Callable[[SnapshotImportRequest], dict],
    auth_dependencies: Sequence,
) -> None:
    @app.get("/api/activity/summary", response_model=ActivitySummaryResponse, dependencies=auth_dependencies)
    def activity_summary(days: int = Query(default=7, ge=1, le=RETENTION_DAYS)) -> dict:
        until = service.wall_now()
        since = until - timedelta(days=days)
        return summarize(activity_store.rows(since.timestamp(), until.timestamp()), since, until)

    @app.get("/api/activity/transmissions", response_model=list[TransmissionResponse], dependencies=auth_dependencies)
    def recent_transmissions(limit: int = Query(default=50, ge=1, le=500)) -> list[TransmissionResponse]:
        return [
            TransmissionResponse(started_at=datetime.fromtimestamp(r.started_at), duration=r.duration, timed_out=r.timed_out)
            for r in activity_store.recent("rx", limit)
        ]

    @app.get("/api/snapshot", response_model=SnapshotModel, dependencies=auth_dependencies)
    def get_snapshot() -> SnapshotModel:
        return SnapshotModel(**service.export_snapshot())

    @app.post("/api/snapshot", response_model=SnapshotModel, dependencies=auth_dependencies)
    async def post_snapshot(snapshot: SnapshotImportRequest) -> SnapshotModel:
        service.import_snapshot(snapshot_to_import(snapshot))
        return SnapshotModel(**service.export_snapshot())
