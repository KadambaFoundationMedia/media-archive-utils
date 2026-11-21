"""Cache transcripts in the local DB by content hash, and write the Markdown file."""

from __future__ import annotations

import logging
from collections import Counter
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from mam.config import Settings, get_settings
from mam.helpers.local_db import models as m
from mam.helpers.transcriber.backends import run_backend
from mam.helpers.transcriber.models import Segment, Transcript, format_timestamp

log = logging.getLogger(__name__)


def load_transcript(session: Session, content_sha256: str) -> Transcript | None:
    run = session.scalar(
        select(m.TranscriptRun).where(m.TranscriptRun.content_sha256 == content_sha256)
    )
    if run is None:
        return None
    rows = session.scalars(
        select(m.TranscriptSegment)
        .where(m.TranscriptSegment.content_sha256 == content_sha256)
        .order_by(m.TranscriptSegment.idx)
    ).all()
    segments = [
        Segment(r.start_ms / 1000, r.end_ms / 1000, r.text, r.language, r.confidence, r.words)
        for r in rows
    ]
    return _finish(
        Transcript(
            segments=segments,
            language=None,
            engine=run.engine,
            engine_version=run.engine_version,
            model=run.model,
            model_hash=run.model_hash,
            backend=run.backend,
            fallback=run.fallback,
            content_sha256=content_sha256,
        )
    )


def _finish(t: Transcript) -> Transcript:
    """Main language and the list of detected languages, by speaking time."""
    time: Counter[str] = Counter()
    for s in t.segments:
        if s.language:
            time[s.language] += max(0.0, s.end - s.start)
    t.languages = [lang for lang, _ in time.most_common()]
    t.language = t.language or (t.languages[0] if t.languages else None)
    return t


def _store(session: Session, t: Transcript) -> None:
    session.execute(
        delete(m.TranscriptSegment).where(m.TranscriptSegment.content_sha256 == t.content_sha256)
    )
    session.execute(
        delete(m.TranscriptRun).where(m.TranscriptRun.content_sha256 == t.content_sha256)
    )
    session.add(
        m.TranscriptRun(
            content_sha256=t.content_sha256,
            engine=t.engine,
            engine_version=t.engine_version,
            model=t.model,
            model_hash=t.model_hash,
            backend=t.backend,
            fallback=t.fallback,
        )
    )
    for idx, s in enumerate(t.segments):
        session.add(
            m.TranscriptSegment(
                content_sha256=t.content_sha256,
                idx=idx,
                start_ms=round(s.start * 1000),
                end_ms=round(s.end * 1000),
                text=s.text,
                language=s.language,
                confidence=s.confidence,
                words=s.words,
            )
        )
    session.flush()


def transcribe(
    session: Session,
    audio: Path,
    content_sha256: str,
    settings: Settings | None = None,
    language: str | None = None,
    force: bool = False,
) -> tuple[Transcript, bool]:
    """Transcribe `audio` (or reuse the cached transcript of the same content).

    Returns (transcript, cached). The session is committed before the long run, so the
    database is not held while the model works.
    """
    settings = settings or get_settings()
    if not force:
        cached = load_transcript(session, content_sha256)
        if cached is not None:
            return cached, True
    session.commit()
    transcript = run_backend(audio, settings, language)
    transcript.content_sha256 = content_sha256
    _finish(transcript)
    _store(session, transcript)
    return transcript, False


# ------------------------------------------------------------------- Markdown
def markdown_text(t: Transcript, title: str) -> str:
    lines = [
        f"# {title}",
        "",
        f"<!-- engine: {t.engine} {t.engine_version or ''} | model: {t.model} | "
        f"backend: {t.backend} | language: {t.language or '?'} -->",
        "",
    ]
    lines += [f"[{format_timestamp(s.start)}] {s.text}" for s in t.segments]
    return "\n".join(lines) + "\n"


def write_markdown(t: Transcript, path: Path, title: str) -> Path:
    """`transcriptions/<final-base-name>.md` (SPEC.md section 5)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(markdown_text(t, title), encoding="utf-8")
    return path
