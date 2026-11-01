"""The normalized transcript contract both backends produce."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class Segment:
    start: float  # seconds
    end: float
    text: str
    language: str | None = None
    confidence: float | None = None  # 0..1, None when the backend gives none


@dataclass(frozen=True)
class TranscriptInfo:
    """Binding metadata of one run: engine, version, model hash, backend, fallback evidence."""

    engine: str
    engine_version: str | None
    model: str
    model_hash: str | None
    backend: str  # metal | cpu | mlx
    fallback: dict | None = None


class Transcriber(Protocol):
    def info(self) -> TranscriptInfo: ...

    def transcribe(self, audio: Path) -> list[Segment]: ...
