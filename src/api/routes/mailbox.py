"""Voice mailbox boxes and messages."""
from __future__ import annotations

import collections
from datetime import datetime
from pathlib import Path
from typing import Sequence

from fastapi import FastAPI, HTTPException
from fastapi import Path as UrlPath
from fastapi.responses import FileResponse

from ..mailbox import MailboxStore
from ..models import (
    MailboxBoxRequest,
    MailboxBoxResponse,
    MailboxMessageResponse,
    MailboxResponse,
)
from ..transcripts import callsigns, read_transcript


def register(
    app: FastAPI,
    *,
    mailbox_store: MailboxStore,
    admin_dependencies: Sequence,
) -> None:
    def mailbox_response() -> MailboxResponse:
        messages = mailbox_store.messages()
        counts = collections.Counter(m.box for m in messages)
        return MailboxResponse(
            boxes=[
                MailboxBoxResponse(box=box, name=info["name"], pin_set=bool(info.get("pin")), messages=counts[box])
                for box, info in mailbox_store.boxes().items()
            ],
            messages=[
                MailboxMessageResponse(
                    id=m.id, box=m.box, left_at=datetime.fromtimestamp(m.left_at), duration=m.duration,
                    transcript=(text := read_transcript(mailbox_store, m.id)), callsigns=callsigns(text or ""),
                )
                for m in reversed(messages)
            ],
        )

    @app.get("/api/mailbox", response_model=MailboxResponse, dependencies=admin_dependencies)
    def get_mailbox() -> MailboxResponse:
        return mailbox_response()

    @app.put("/api/mailbox/boxes/{box}", response_model=MailboxResponse, dependencies=admin_dependencies)
    def put_mailbox_box(body: MailboxBoxRequest, box: str = UrlPath(pattern=r"^[0-9]{1,6}$")) -> MailboxResponse:
        if box not in mailbox_store.boxes() and body.pin is None:
            raise HTTPException(status_code=400, detail="A new mailbox needs a PIN")
        mailbox_store.set_box(box, body.name.strip(), body.pin)
        return mailbox_response()

    @app.delete("/api/mailbox/boxes/{box}", response_model=MailboxResponse, dependencies=admin_dependencies)
    def delete_mailbox_box(box: str) -> MailboxResponse:
        if not mailbox_store.delete_box(box):
            raise HTTPException(status_code=404, detail=f"No mailbox {box!r}")
        return mailbox_response()

    def mailbox_message_path(message_id: str) -> Path:
        try:
            path = mailbox_store.path_for(message_id)
        except KeyError:
            path = None
        if path is None or not path.exists() or message_id.startswith("mailbox-play-"):
            raise HTTPException(status_code=404, detail=f"No message with id {message_id!r}")
        return path

    @app.get("/api/mailbox/messages/{message_id}/audio", dependencies=admin_dependencies)
    def get_mailbox_message_audio(message_id: str) -> FileResponse:
        return FileResponse(mailbox_message_path(message_id), media_type="audio/wav")

    @app.delete("/api/mailbox/messages/{message_id}", response_model=MailboxResponse, dependencies=admin_dependencies)
    def delete_mailbox_message(message_id: str) -> MailboxResponse:
        mailbox_message_path(message_id)
        mailbox_store.delete_message(message_id)
        return mailbox_response()
