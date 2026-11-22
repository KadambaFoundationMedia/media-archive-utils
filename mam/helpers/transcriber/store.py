"""Cache transcripts in the local DB by content hash, and write the Markdown file."""

from __future__ import annotations

import contextlib
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
def srt_time(seconds: float) -> str:
    """SubRip time: 00:01:25,000."""
    return format_timestamp(seconds).replace(".", ",")


def srt_text(t: Transcript) -> str:
    """The transcript as SubRip (.srt): numbered cues with start --> end and the text."""
    cues = []
    for number, seg in enumerate(t.segments, start=1):
        end = max(seg.end, seg.start + 0.5)
        cues.append(f"{number}\n{srt_time(seg.start)} --> {srt_time(end)}\n{seg.text.strip()}\n")
    return "\n".join(cues)


def write_srt(t: Transcript, path: Path) -> Path:
    """`transcriptions/<category folder>/<final-base-name>.srt` (SPEC.md section 5)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(srt_text(t), encoding="utf-8")
    return path


def transcript_file(settings: Settings, processed: Path) -> Path:
    """`transcriptions/<category folder>/<name>.srt` for an audio copy in processed-files."""
    folder = Path()
    root = settings.media_processed_files_path.resolve()
    with contextlib.suppress(ValueError):  # outside processed-files (a dry run): the root
        folder = processed.resolve().parent.relative_to(root)
    return settings.transcriptions_path / folder / f"{processed.stem}.srt"


def follow_audio(settings: Settings, old: Path | None, new: Path) -> Path | None:
    """Move the transcript along when its audio was renamed or filed in another folder.

    Never overwrites: a transcript already at the new place stays and the old one is left.
    """
    if old is None or old == new:
        return None
    moved = None
    for suffix in (".srt", ".md"):  # .md is the format before 2026-11-22
        src = transcript_file(settings, old).with_suffix(suffix)
        dst = transcript_file(settings, new).with_suffix(suffix)
        if src == dst or not src.exists() or dst.exists():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dst)
        moved = moved or dst
    return moved
