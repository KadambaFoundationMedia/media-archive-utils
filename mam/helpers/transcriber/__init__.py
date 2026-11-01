"""Shared transcriber: one interface, two backends (ARCHITECTURE.md), cached per content hash."""

from mam.helpers.transcriber.backends import get_transcriber
from mam.helpers.transcriber.base import Segment, Transcriber, TranscriptInfo
from mam.helpers.transcriber.store import get_or_transcribe, load_transcript

__all__ = [
    "Segment",
    "Transcriber",
    "TranscriptInfo",
    "get_or_transcribe",
    "get_transcriber",
    "load_transcript",
]
