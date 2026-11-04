"""Uploaded clips, repeater/monitor recordings, and transcription status."""
from __future__ import annotations

import dataclasses
from datetime import datetime
from pathlib import Path
from typing import Optional, Sequence

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from ..assets import AssetKind, AudioAssetStore
from ..models import (
    AssetResponse,
    RecordingResponse,
    RecordingSort,
    RecordingSource,
    RecordingSummaryResponse,
    TranscriptionStatus,
)
from ..recordings import RecordingInfo, RecordingStore
from ..service import RepeaterService
from ..transcripts import Transcriber, callsigns, read_transcript

RECORDING_SEARCH_LIMIT = 5000


def _asset_response(asset) -> AssetResponse:
    return AssetResponse(**dataclasses.asdict(asset))


def _recording_response(recording: RecordingInfo, transcript: Optional[str]) -> RecordingResponse:
    return RecordingResponse(
        id=recording.id,
        started_at=datetime.fromtimestamp(recording.started_at),
        duration=recording.duration,
        transcript=transcript,
        callsigns=callsigns(transcript or ""),
    )


def _ms_bound(value: Optional[datetime]) -> Optional[int]:
    if value is None:
        return None
    return int(value.timestamp() * 1000)


def _matches(response: RecordingResponse, q: str, callsign: str) -> bool:
    if callsign and callsign.upper() not in response.callsigns:
        return False
    if not q:
        return True
    return q.upper() in response.callsigns or q.lower() in (response.transcript or "").lower()


def _sort_recordings(items: list[RecordingResponse], sort: RecordingSort) -> list[RecordingResponse]:
    """Sort recordings. Callsign sorts put clips with no heard callsign last —
    transcripts only catch what was said, and many radios never ID into the mic."""
    if sort == "newest":
        return sorted(items, key=lambda item: item.id, reverse=True)
    if sort == "oldest":
        return sorted(items, key=lambda item: item.id)
    known = [item for item in items if item.callsigns]
    unknown = [item for item in items if not item.callsigns]
    known.sort(
        key=lambda item: (item.callsigns[0], item.id),
        reverse=(sort == "callsign_desc"),
    )
    unknown.sort(key=lambda item: item.id, reverse=True)
    return known + unknown


def register(
    app: FastAPI,
    *,
    service: RepeaterService,
    assets_store: AudioAssetStore,
    recordings: RecordingStore,
    monitor_recordings: RecordingStore,
    transcriber: Transcriber,
    auth_dependencies: Sequence,
) -> None:
    @app.get("/api/assets", response_model=list[AssetResponse], dependencies=auth_dependencies)
    def list_assets() -> list[AssetResponse]:
        return [_asset_response(a) for a in assets_store.list_assets()]

    @app.post("/api/assets", response_model=AssetResponse, dependencies=auth_dependencies)
    async def upload_asset(kind: AssetKind = Form(...), file: UploadFile = File(...)) -> AssetResponse:
        if not (file.filename or "").lower().endswith(".wav"):
            raise HTTPException(status_code=400, detail="Only .wav files are accepted")
        content = await file.read()
        asset = assets_store.save_asset(kind, file.filename, content)
        return _asset_response(asset)

    @app.delete("/api/assets/{asset_id}", dependencies=auth_dependencies)
    def delete_asset(asset_id: str) -> dict:
        if asset_id not in {a.id for a in assets_store.list_assets()}:
            raise HTTPException(status_code=404, detail=f"No asset with id {asset_id!r}")
        assets_store.delete_asset(asset_id)
        return {"deleted": asset_id}

    @app.get("/api/assets/{asset_id}/audio", dependencies=auth_dependencies)
    def get_asset_audio(asset_id: str) -> FileResponse:
        if not assets_store.has(asset_id):
            raise HTTPException(status_code=404, detail=f"No asset with id {asset_id!r}")
        return FileResponse(assets_store.path_for(asset_id), media_type="audio/wav")

    def recording_store(source: str) -> RecordingStore:
        return monitor_recordings if source == "monitor" else recordings

    def recording_path(recording_id: str, source: str = "repeater") -> Path:
        try:
            path = recording_store(source).path_for(recording_id)
        except KeyError:
            path = None
        if path is None or not path.exists():
            raise HTTPException(status_code=404, detail=f"No recording with id {recording_id!r}")
        return path

    def recording_filters(
        after: Optional[datetime],
        before: Optional[datetime],
        q: str,
        callsign: str,
    ) -> tuple[Optional[int], Optional[int], str, str]:
        if after is not None and before is not None and after >= before:
            raise HTTPException(status_code=400, detail="after must be earlier than before")
        return _ms_bound(after), _ms_bound(before), q.strip(), callsign.strip().upper()

    def matching_recordings(
        store: RecordingStore,
        *,
        after_ms: Optional[int],
        before_ms: Optional[int],
        q: str,
        callsign: str,
    ) -> list[RecordingResponse]:
        found: list[RecordingResponse] = []
        for recording in store.list(RECORDING_SEARCH_LIMIT, after_ms=after_ms, before_ms=before_ms):
            response = _recording_response(recording, read_transcript(store, recording.id))
            if _matches(response, q, callsign):
                found.append(response)
        return found

    def page_recordings(
        items: list[RecordingResponse],
        *,
        sort: RecordingSort,
        limit: Optional[int],
        offset: int,
    ) -> list[RecordingResponse]:
        ordered = _sort_recordings(items, sort)
        if offset:
            ordered = ordered[offset:]
        if limit is not None:
            ordered = ordered[:limit]
        return ordered

    @app.get("/api/recordings", response_model=list[RecordingResponse], dependencies=auth_dependencies)
    def list_recordings(
        limit: int = Query(default=50, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        q: str = Query(default="", max_length=100),
        callsign: str = Query(default="", max_length=20),
        after: Optional[datetime] = None,
        before: Optional[datetime] = None,
        sort: RecordingSort = "newest",
        source: RecordingSource = "repeater",
    ) -> list[RecordingResponse]:
        """`q` searches transcripts; `callsign` matches extracted callsigns exactly.

        Callsign sorts use the first callsign heard in the transcript. Clips with
        none (common without a radio that IDs into the mic) sort last.
        """
        after_ms, before_ms, q, callsign = recording_filters(after, before, q, callsign)
        store = recording_store(source)
        # Newest-first with no text filter can page off the directory order.
        if sort == "newest" and not q and not callsign:
            return [
                _recording_response(recording, read_transcript(store, recording.id))
                for recording in store.list(limit, offset=offset, after_ms=after_ms, before_ms=before_ms)
            ]
        return page_recordings(
            matching_recordings(
                store,
                after_ms=after_ms,
                before_ms=before_ms,
                q=q,
                callsign=callsign,
            ),
            sort=sort,
            limit=limit,
            offset=offset,
        )

    @app.get("/api/recordings/summary", response_model=RecordingSummaryResponse, dependencies=auth_dependencies)
    def recordings_summary(
        q: str = Query(default="", max_length=100),
        callsign: str = Query(default="", max_length=20),
        after: Optional[datetime] = None,
        before: Optional[datetime] = None,
        source: RecordingSource = "repeater",
    ) -> RecordingSummaryResponse:
        after_ms, before_ms, q, callsign = recording_filters(after, before, q, callsign)
        store = recording_store(source)
        if not q and not callsign:
            count, total_duration = store.summarize(after_ms=after_ms, before_ms=before_ms)
            transcribed = 0
            for recording in store.list(RECORDING_SEARCH_LIMIT, after_ms=after_ms, before_ms=before_ms):
                if read_transcript(store, recording.id) is not None:
                    transcribed += 1
            return RecordingSummaryResponse(count=count, total_duration=total_duration, transcribed=transcribed)

        matched = matching_recordings(
            store,
            after_ms=after_ms,
            before_ms=before_ms,
            q=q,
            callsign=callsign,
        )
        return RecordingSummaryResponse(
            count=len(matched),
            total_duration=sum(item.duration for item in matched),
            transcribed=sum(1 for item in matched if item.transcript is not None),
        )

    @app.get("/api/transcription", response_model=TranscriptionStatus, dependencies=auth_dependencies)
    def get_transcription_status() -> TranscriptionStatus:
        return TranscriptionStatus(
            engine=service.config.transcription_engine,
            vosk_installed=transcriber.vosk.installed(),
            vosk_model=transcriber.vosk.model_present(),
            vosk_model_dir=str(transcriber.vosk.model_dir),
            api_key_set=bool(transcriber.api_key),
            pending=len(transcriber.pending()) if service.config.transcription_engine != "off" else 0,
            transcribed=transcriber.status.transcribed,
            last_error=transcriber.status.last_error,
        )

    @app.get("/api/recordings/{recording_id}/audio", dependencies=auth_dependencies)
    def get_recording_audio(recording_id: str, source: RecordingSource = "repeater") -> FileResponse:
        return FileResponse(recording_path(recording_id, source), media_type="audio/wav")

    @app.delete("/api/recordings/{recording_id}", dependencies=auth_dependencies)
    def delete_recording(recording_id: str, source: RecordingSource = "repeater") -> dict:
        recording_path(recording_id, source)
        recording_store(source).delete(recording_id)
        return {"deleted": recording_id}
