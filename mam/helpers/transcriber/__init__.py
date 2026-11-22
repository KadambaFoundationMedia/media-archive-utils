"""Transcriber helper: one interface, two backends (mlx-whisper on Apple Silicon, whisper.cpp).

`transcribe(session, audio, content_sha256)` returns the same `Transcript` from either backend
and caches it in the local database by the content hash of the audio, so a re-run, the
class-discoverer and the later tools never transcribe the same content twice.
"""

from mam.helpers.transcriber.chunking import transcript_quality
from mam.helpers.transcriber.models import Segment, Transcript, format_timestamp
from mam.helpers.transcriber.store import (
    follow_audio,
    load_transcript,
    srt_text,
    transcribe,
    transcript_file,
    write_srt,
)

__all__ = [
    "transcript_quality",
    "follow_audio",
    "transcript_file",
    "Segment",
    "Transcript",
    "format_timestamp",
    "load_transcript",
    "srt_text",
    "transcribe",
    "write_srt",
]
