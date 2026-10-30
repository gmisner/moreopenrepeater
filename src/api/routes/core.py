"""Live status, repeater config, interface boards, sound levels, lockout."""
from __future__ import annotations

import asyncio
import dataclasses
import logging
from typing import Awaitable, Callable, Sequence

from fastapi import FastAPI, HTTPException, Request

from ..audit import describe_config_change
from ..boards import WIRING_FIELDS, alsa_card, apply_mixer, load_boards, preset_changes
from ..mixer import levels_on_card, read_volumes, set_volume, side_card, side_volumes
from ..models import (
    BoardApplyRequest,
    BoardApplyResponse,
    BoardResponse,
    ConfigResponse,
    ConfigUpdateRequest,
    SoundLevelUpdateRequest,
    SoundLevelsResponse,
    StatusResponse,
)
from ..service import RepeaterService
from playout.tts import TTSError


def register(
    app: FastAPI,
    *,
    service: RepeaterService,
    run_mixer: Callable,
    render_and_queue: Callable[[str], Awaitable[None]],
    status_response: Callable[..., StatusResponse],
    config_response: Callable[..., ConfigResponse],
    auth_dependencies: Sequence,
    transmit_dependencies: Sequence,
) -> None:
    @app.get("/api/status", response_model=StatusResponse, dependencies=auth_dependencies)
    def get_status() -> StatusResponse:
        return status_response(service.snapshot())

    @app.get("/api/config", response_model=ConfigResponse, dependencies=auth_dependencies)
    def get_config() -> ConfigResponse:
        return config_response(service)

    @app.put("/api/config", response_model=ConfigResponse, dependencies=auth_dependencies)
    async def put_config(update: ConfigUpdateRequest, request: Request) -> ConfigResponse:
        clear_fields = {name for name in ConfigUpdateRequest.model_fields if name.startswith("clear_")}
        overrides = update.model_dump(exclude=clear_fields, exclude_none=True)
        for clear_field in clear_fields:
            if getattr(update, clear_field):
                overrides[clear_field.removeprefix("clear_")] = None
        saved = service.saved_config
        if saved.board_preset and any(overrides.get(name, getattr(saved, name)) != getattr(saved, name) for name in WIRING_FIELDS):
            overrides["board_preset"] = ""
        request.state.audit_detail = describe_config_change(dataclasses.asdict(saved), overrides)
        service.update_config(**overrides)
        return config_response(service)

    boards = {board.id: board for board in load_boards()}

    @app.get("/api/boards", response_model=list[BoardResponse], dependencies=auth_dependencies)
    def get_boards() -> list[BoardResponse]:
        return [
            BoardResponse(
                **{name: getattr(board, name) for name in ("id", "name", "maker", "kind", "notes", "unsupported")},
                device_hints=list(board.device_hints),
                two_port=board.link is not None,
                mixer=list(board.mixer),
            )
            for board in boards.values()
        ]

    @app.post("/api/boards/{board_id}/apply", response_model=BoardApplyResponse, dependencies=auth_dependencies)
    async def apply_board(board_id: str, body: BoardApplyRequest, request: Request) -> BoardApplyResponse:
        board = boards.get(board_id)
        if board is None:
            raise HTTPException(status_code=404, detail="No such interface board")
        if board.unsupported:
            raise HTTPException(status_code=409, detail=f"{board.name} isn't supported yet: {board.unsupported}")
        changes = preset_changes(board, body.input_device, body.output_device, body.link_input_device, body.link_output_device)
        request.state.audit_detail = board.name
        service.update_config(**changes)
        config = service.saved_config
        mixer, skipped = [], []
        if body.set_mixer and board.mixer:
            devices = [config.audio_output_device]
            if config.link_radio_enabled and board.link:
                devices.append(config.link_radio_output_device)
            cards = []
            for device in devices:
                card = alsa_card(device)
                if card is None:
                    skipped.append(device or "System default")
                elif card not in cards:
                    cards.append(card)
            loop = asyncio.get_running_loop()
            results = await loop.run_in_executor(None, apply_mixer, cards, board.mixer, run_mixer)
            mixer = [dataclasses.asdict(result) for result in results]
            output_card = side_card(config, "output")
            if output_card is not None:
                try:
                    inputs, outputs = await loop.run_in_executor(
                        None, levels_on_card, output_card, {control for control, _ in board.mixer}, run_mixer
                    )
                except OSError as error:
                    logging.getLogger("moreopenrepeater.mixer").warning("couldn't read back the board's levels: %s", error)
                else:
                    if side_card(config, "input") != output_card:
                        inputs = {}
                    service.update_config(audio_input_levels=inputs, audio_output_levels=outputs)
        return BoardApplyResponse(config=config_response(service), mixer=mixer, mixer_skipped=skipped)

    def sound_levels() -> SoundLevelsResponse:
        config = service.saved_config
        levels, notes = [], []
        read: dict[int, list] = {}
        for side, saved in (("input", config.audio_input_levels), ("output", config.audio_output_levels)):
            card = side_card(config, side)
            if card is None:
                notes.append(f"The {side} is the system default device; pick a specific sound card to set its levels.")
                continue
            if card not in read:
                try:
                    read[card] = read_volumes(card, run_mixer)
                except OSError as error:
                    notes.append(f"Couldn't read sound card {card}'s levels: {error}")
                    read[card] = []
            for volume in side_volumes(side, read[card]):
                levels.append({
                    **dataclasses.asdict(volume), "side": side, "card": card,
                    "saved": saved.get(volume.control) == volume.value,
                })
        return SoundLevelsResponse(levels=levels, notes=notes)

    @app.get("/api/audio/levels", response_model=SoundLevelsResponse, dependencies=auth_dependencies)
    async def get_sound_levels() -> SoundLevelsResponse:
        return await asyncio.get_running_loop().run_in_executor(None, sound_levels)

    @app.put("/api/audio/levels", response_model=SoundLevelsResponse, dependencies=auth_dependencies)
    async def put_sound_level(body: SoundLevelUpdateRequest, request: Request) -> SoundLevelsResponse:
        config = service.saved_config
        card = side_card(config, body.side)
        if card is None:
            raise HTTPException(status_code=409, detail=f"Pick a specific {body.side} sound card first")
        loop = asyncio.get_running_loop()
        try:
            volumes = await loop.run_in_executor(None, read_volumes, card, run_mixer)
        except OSError as error:
            raise HTTPException(status_code=503, detail=f"Couldn't read sound card {card}'s levels: {error}")
        volume = next((v for v in side_volumes(body.side, volumes) if v.control == body.control), None)
        if volume is None:
            raise HTTPException(status_code=404, detail=f"Sound card {card} has no {body.control} {body.side} level")
        value = min(max(body.value, volume.min), volume.max)
        error = await loop.run_in_executor(None, set_volume, card, volume.control, volume.direction, value, run_mixer)
        if error:
            raise HTTPException(status_code=503, detail=f"Couldn't set {volume.control}: {error}")
        field_name = f"audio_{body.side}_levels"
        service.update_config(**{field_name: {**getattr(service.saved_config, field_name), volume.control: value}})
        request.state.audit_detail = f"{volume.control} {body.side} {value}"
        return await loop.run_in_executor(None, sound_levels)

    @app.post("/api/audio/test-id", response_model=StatusResponse, dependencies=transmit_dependencies)
    async def test_id() -> StatusResponse:
        if not service.saved_config.audio_enabled:
            raise HTTPException(status_code=409, detail="Turn on live audio first")
        try:
            await render_and_queue("id")
        except TTSError as error:
            raise HTTPException(status_code=503, detail=str(error))
        return status_response(service.snapshot())

    @app.post("/api/lockout/clear", response_model=StatusResponse, dependencies=auth_dependencies)
    async def clear_lockout() -> StatusResponse:
        if not service.controller.locked_out:
            raise HTTPException(status_code=409, detail="The repeater isn't locked out")
        service.clear_lockout()
        return status_response(service.snapshot())
