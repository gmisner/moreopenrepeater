"""TTS preview, device lists, and live audio engine diagnostics."""
from __future__ import annotations

from typing import Callable, Sequence

from fastapi import FastAPI, HTTPException, Response
from playout.renderer import ClipRenderer, UnknownClipError
from playout.tts import TTSError
from playout.wav import encode_wav
from controller.modes import effective_config
from controller.state_machine import RepeaterConfig

from ..live_audio import LiveAudio
from ..models import (
    AudioDeviceResponse,
    AudioEngineResponse,
    AudioGlitchResponse,
    AudioPreviewRequest,
    ReceiverOpeningResponse,
    SerialPortResponse,
    TTSInfoResponse,
)
from ..service import RepeaterService

MAX_PREVIEW_CLIP_LENGTH = 2000


def register(
    app: FastAPI,
    *,
    service: RepeaterService,
    renderer: ClipRenderer,
    live_audio: LiveAudio,
    audio_devices: Callable[[], list[dict]],
    serial_ports: Callable[[], list[dict]],
    apply_overrides: Callable[[RepeaterConfig, dict], RepeaterConfig],
    auth_dependencies: Sequence,
) -> None:
    @app.get("/api/audio/tts", response_model=TTSInfoResponse, dependencies=auth_dependencies)
    def get_tts_info() -> TTSInfoResponse:
        return TTSInfoResponse(engine=renderer.tts.name if renderer.tts is not None else None)

    # Plain `def`: FastAPI runs it in a worker thread, so a slow TTS render
    # doesn't stall the event loop (and with it the controller tick).
    @app.post("/api/audio/preview", response_class=Response, dependencies=auth_dependencies)
    def audio_preview(body: AudioPreviewRequest) -> Response:
        if len(body.clip) > MAX_PREVIEW_CLIP_LENGTH:
            raise HTTPException(status_code=400, detail="Text is too long to preview")
        config = effective_config(apply_overrides(service.saved_config, body.config), net_active=body.net)
        try:
            samples = renderer.render(body.clip, config)
        except UnknownClipError:
            raise HTTPException(status_code=404, detail=f"Unknown clip {body.clip!r}")
        except TTSError as error:
            raise HTTPException(status_code=503, detail=str(error))
        return Response(encode_wav(samples, renderer.sample_rate), media_type="audio/wav")

    @app.get("/api/serial/ports", response_model=list[SerialPortResponse], dependencies=auth_dependencies)
    def get_serial_ports() -> list[dict]:
        return serial_ports()

    @app.get("/api/audio/devices", response_model=list[AudioDeviceResponse], dependencies=auth_dependencies)
    def get_audio_devices() -> list[dict]:
        try:
            return audio_devices()
        except Exception as error:  # PortAudio errors
            raise HTTPException(status_code=503, detail=f"couldn't list audio devices: {error}")

    @app.get("/api/audio/engine", response_model=AudioEngineResponse, dependencies=auth_dependencies)
    def get_audio_engine() -> dict:
        return live_audio.status()

    @app.get("/api/audio/openings", response_model=list[ReceiverOpeningResponse], dependencies=auth_dependencies)
    def get_receiver_openings() -> list[dict]:
        return list(reversed(live_audio.openings))

    @app.get("/api/audio/glitches", response_model=list[AudioGlitchResponse], dependencies=auth_dependencies)
    def get_audio_glitches() -> list[dict]:
        return list(reversed(live_audio.glitches))
