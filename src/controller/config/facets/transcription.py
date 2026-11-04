from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
"""TranscriptionConfig: config facet (flat wire field names)."""


@dataclass(frozen=True)
class TranscriptionConfig:
    # Transcripts of recordings and mailbox messages (api.transcripts).
    transcription_engine: Literal['off', 'vosk', 'openai'] = 'off'
    transcription_url: str = 'https://api.openai.com/v1/audio/transcriptions'
    transcription_model: str = 'whisper-1'
