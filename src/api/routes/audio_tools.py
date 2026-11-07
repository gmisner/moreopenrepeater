"""TTS preview, device lists, and live audio engine diagnostics."""
from __future__ import annotations

from typing import Callable, Sequence

from fastapi import FastAPI, File, HTTPException, Query, Response, UploadFile
from playout.renderer import ClipRenderer, UnknownClipError
from playout.tts import DEFAULT_TTS_SPEED, MAX_TTS_SPEED, MIN_TTS_SPEED, TTSError, list_tts_voices
from playout.wav import WavError, decode_wav, encode_wav
from controller.modes import effective_config
from controller.state_machine import RepeaterConfig

from audio_io.score import score_clip

from ..live_audio import LiveAudio
from ..models import (
    AudioDeviceResponse,
    AudioEngineResponse,
    AudioGlitchResponse,
    AudioPreviewRequest,
    AudioScoreResponse,
    ReceiverOpeningResponse,
    SerialPortResponse,
    TTSInfoResponse,
    TTSVoiceOption,
)
from ..service import RepeaterService

MAX_PREVIEW_CLIP_LENGTH = 2000
MAX_SCORE_WAV_BYTES = 5_000_000
MIN_RECORD_SECONDS = 1.0
MAX_RECORD_SECONDS = 30.0


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
        engine = renderer.tts
        voices = [
            TTSVoiceOption(
                id=voice.id,
                label=voice.label,
                locale=voice.locale,
                locale_label=voice.locale_label,
                gender=voice.gender,
            )
            for voice in list_tts_voices(engine)
        ]
        return TTSInfoResponse(
            engine=engine.name if engine is not None else None,
            voices=voices,
            min_speed=MIN_TTS_SPEED,
            max_speed=MAX_TTS_SPEED,
            default_speed=DEFAULT_TTS_SPEED,
        )

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

    @app.post("/api/audio/score", response_model=AudioScoreResponse, dependencies=auth_dependencies)
    async def score_uploaded_wav(file: UploadFile = File(...)) -> dict:
        """Score an uploaded WAV the same way a live capture is scored."""
        data = await file.read(MAX_SCORE_WAV_BYTES + 1)
        if len(data) > MAX_SCORE_WAV_BYTES:
            raise HTTPException(status_code=413, detail="WAV is too large (max 5 MB)")
        if not data:
            raise HTTPException(status_code=400, detail="Empty upload")
        try:
            samples, rate = decode_wav(data)
        except WavError as error:
            raise HTTPException(status_code=400, detail=str(error)) from None
        return score_clip(samples, rate)

    @app.post("/api/audio/record-score", response_model=AudioScoreResponse, dependencies=auth_dependencies)
    async def record_and_score(
        seconds: float = Query(default=5.0, ge=MIN_RECORD_SECONDS, le=MAX_RECORD_SECONDS),
    ) -> dict:
        """Capture RX for `seconds` from the live engine and return score stats."""
        try:
            samples, rate = await live_audio.record_rx(seconds)
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail=str(error)) from None
        return score_clip(samples, rate)
