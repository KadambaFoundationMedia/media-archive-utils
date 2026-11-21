"""The normalized transcript contract shared by both backends."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Segment:
    start: float  # seconds
    end: float
    text: str
    language: str | None = None
    confidence: float | None = None
    words: list[dict[str, Any]] | None = None


@dataclass
class Transcript:
    segments: list[Segment]
    language: str | None  # main language (ISO 639-1), as detected
    engine: str  # mlx-whisper | whisper.cpp
    engine_version: str | None
    model: str
    model_hash: str | None
    backend: str  # mlx | metal | cpu
    fallback: dict[str, Any] | None = None
    content_sha256: str = ""
    languages: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(s.text.strip() for s in self.segments)

    def text_between(self, start: float, end: float) -> str:
        return " ".join(s.text.strip() for s in self.segments if s.end > start and s.start < end)


def format_timestamp(seconds: float) -> str:
    """[hh:mm:ss.mmm] as SPEC.md section 5 requires."""
    ms = round(seconds * 1000)
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"
