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
    RecordingSource,
    TranscriptionStatus,
)
from ..recordings import RecordingInfo, RecordingStore
from ..service import RepeaterService
from ..transcripts import Transcriber, callsigns, read_transcript

RECORDING_SEARCH_LIMIT = 500


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

    @app.get("/api/recordings", response_model=list[RecordingResponse], dependencies=auth_dependencies)
    def list_recordings(
        limit: int = Query(default=50, ge=1, le=500),
        q: str = Query(default="", max_length=100),
        source: RecordingSource = "repeater",
    ) -> list[RecordingResponse]:
        """`q` searches the transcripts (words, or a callsign however it was said)."""
        q = q.strip()
        store = recording_store(source)
        found = []
        for recording in store.list(RECORDING_SEARCH_LIMIT if q else limit):
            response = _recording_response(recording, read_transcript(store, recording.id))
            if q and q.upper() not in response.callsigns and q.lower() not in (response.transcript or "").lower():
                continue
            found.append(response)
            if len(found) >= limit:
                break
        return found

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
