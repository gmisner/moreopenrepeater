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


    @app.get("/api/activity/history24h", dependencies=auth_dependencies)
    def activity_history_24h() -> dict:
        """Rolling 24 one-hour bins of recorded RX and TX airtime.

        Clip intervals to each hour and the requested 24h window; a
        transmission crossing an hour boundary contributes to both bins.
        """
        until = service.wall_now()
        end = until.timestamp()
        start = end - 24 * 3600
        bins = [
            {"started_at": datetime.fromtimestamp(start + i * 3600).isoformat(), "rx_seconds": 0.0, "tx_seconds": 0.0, "rx_count": 0}
            for i in range(24)
        ]
        # Rows are keyed by transmission start; include long events starting
        # before the window by querying an extra day, then clip overlap.
        for row in activity_store.rows(start - 24 * 3600, end):
            if row.kind not in ("rx", "tx"):
                continue
            row_start = max(start, row.started_at)
            row_end = min(end, row.started_at + max(0.0, row.duration))
            if row_end <= row_start:
                continue
            first = max(0, min(23, int((row_start - start) // 3600)))
            last = max(0, min(23, int((row_end - start - 1e-9) // 3600)))
            for i in range(first, last + 1):
                overlap = max(0.0, min(row_end, start + (i + 1) * 3600) - max(row_start, start + i * 3600))
                if row.kind == "rx":
                    bins[i]["rx_seconds"] += overlap
                    if i == first:
                        bins[i]["rx_count"] += 1
                else:
                    bins[i]["tx_seconds"] += overlap
        return {"since": datetime.fromtimestamp(start).isoformat(), "until": until.isoformat(), "bins": bins}

    @app.get("/api/snapshot", response_model=SnapshotModel, dependencies=auth_dependencies)
    def get_snapshot() -> SnapshotModel:
        return SnapshotModel(**service.export_snapshot())

    @app.post("/api/snapshot", response_model=SnapshotModel, dependencies=auth_dependencies)
    async def post_snapshot(snapshot: SnapshotImportRequest) -> SnapshotModel:
        service.import_snapshot(snapshot_to_import(snapshot))
        return SnapshotModel(**service.export_snapshot())
