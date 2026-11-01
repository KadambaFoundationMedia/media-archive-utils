"""Transcripts are made once per content hash and reused by every tool (SPEC.md section 5)."""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from mam.config import Settings, get_settings
from mam.helpers.local_db import models as m
from mam.helpers.local_db.hashing import sha256
from mam.helpers.transcriber.base import Segment, Transcriber

log = logging.getLogger(__name__)


def load_transcript(session: Session, content_sha: str) -> list[Segment] | None:
    if (
        session.scalar(
            select(m.TranscriptRun.id).where(m.TranscriptRun.content_sha256 == content_sha)
        )
        is None
    ):
        return None
    rows = session.scalars(
        select(m.TranscriptSegment)
        .where(m.TranscriptSegment.content_sha256 == content_sha)
        .order_by(m.TranscriptSegment.idx)
    )
    return [
        Segment(r.start_ms / 1000, r.end_ms / 1000, r.text, r.language, r.confidence) for r in rows
    ]


def get_or_transcribe(
    session: Session,
    audio: Path,
    transcriber: Transcriber,
    settings: Settings | None = None,
    *,
    content_sha: str | None = None,
) -> tuple[list[Segment], bool]:
    """Segments for `audio` and whether they came from the cache."""
    settings = settings or get_settings()
    content_sha = content_sha or sha256(audio)
    cached = load_transcript(session, content_sha)
    if cached is not None:
        return cached, True
    info = transcriber.info()
    segments = transcriber.transcribe(audio)
    session.execute(
        delete(m.TranscriptSegment).where(m.TranscriptSegment.content_sha256 == content_sha)
    )
    for i, seg in enumerate(segments):
        session.add(
            m.TranscriptSegment(
                content_sha256=content_sha,
                idx=i,
                start_ms=round(seg.start * 1000),
                end_ms=round(seg.end * 1000),
                text=seg.text,
                language=seg.language,
                confidence=seg.confidence,
            )
        )
    session.add(
        m.TranscriptRun(
            content_sha256=content_sha,
            engine=info.engine,
            engine_version=info.engine_version,
            model=info.model,
            model_hash=info.model_hash,
            backend=info.backend,
            fallback=info.fallback,
        )
    )
    session.flush()
    write_markdown(audio, segments, settings)
    return segments, False


def write_markdown(audio: Path, segments: list[Segment], settings: Settings) -> Path | None:
    """A readable copy in transcriptions/ (the database is the source of truth)."""
    try:
        settings.transcriptions_path.mkdir(parents=True, exist_ok=True)
        out = settings.transcriptions_path / f"{audio.stem}.md"
        lines = [f"# {audio.name}", ""]
        for s in segments:
            lines.append(f"[{_stamp(s.start)}] {s.text}")
        out.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return out
    except OSError:
        log.warning("Could not write the transcript copy for %s", audio.name)
        return None


def _stamp(seconds: float) -> str:
    h, rest = divmod(int(seconds), 3600)
    mnt, sec = divmod(rest, 60)
    return f"{h}:{mnt:02d}:{sec:02d}" if h else f"{mnt}:{sec:02d}"
