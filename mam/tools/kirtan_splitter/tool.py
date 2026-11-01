"""Kirtan splitter job: transcribe, find the boundary, cut, name, and flag when unsure."""

from __future__ import annotations

import logging
import random
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from mam.config import Settings, get_settings
from mam.errors import MamError
from mam.helpers.audio_optimizer import probe, stream_cut
from mam.helpers.file_organizer import FileOrganizer
from mam.helpers.file_organizer.organizer import assert_writable
from mam.helpers.local_db import models as m
from mam.helpers.local_db.hashing import fingerprint, sha256
from mam.helpers.local_db.repo import record_event, register_derived, set_status
from mam.helpers.transcriber import get_or_transcribe, get_transcriber
from mam.manager.tools import JobContext, ToolOutcome
from mam.tools.kirtan_splitter import phrases
from mam.tools.kirtan_splitter.audio import extract_audio, find_silences
from mam.tools.kirtan_splitter.detect import Decision, detect

log = logging.getLogger(__name__)
TOOL = "kirtan-splitter"
TOLERANCE_S = 1.5  # SPEC.md section 9
MANTRA_CHOICES = [
    phrases.MAHA_MANTRA,
    phrases.RADHA_MADHAVA,
    phrases.PANCA_TATTVA,
    phrases.NRSIMHA,
]


# ------------------------------------------------------------------ timestamps
def format_time(seconds: float) -> str:
    """28:13.048 (or 1:02:03.000)"""
    ms = round(seconds * 1000)
    h, rest = divmod(ms, 3_600_000)
    mnt, rest = divmod(rest, 60_000)
    sec = rest / 1000
    return f"{h}:{mnt:02d}:{sec:06.3f}" if h else f"{mnt}:{sec:06.3f}"


def parse_time(text: str) -> float:
    """'28:13.048', '1:02:03' or '1693.05' (seconds) to seconds."""
    text = text.strip().replace(",", ".")
    if not re.fullmatch(r"\d+(:\d{1,2}){0,2}(\.\d+)?", text):
        raise MamError(f"'{text}' is not a time. Use minutes:seconds, e.g. 28:13.048")
    parts = [float(p) for p in text.split(":")]
    seconds = 0.0
    for p in parts:
        seconds = seconds * 60 + p
    return seconds


# ------------------------------------------------------------------ thresholds
def threshold(session: Session, settings: Settings) -> float:
    """Lowest score that is applied without a review (SPEC.md section 10)."""
    row = session.scalar(
        select(m.Calibration)
        .where(m.Calibration.tool == TOOL, m.Calibration.decision == "boundary")
        .order_by(m.Calibration.id.desc())
    )
    return row.threshold if row else settings.start_threshold


# --------------------------------------------------------------------- the job
def _processed_root(settings: Settings) -> Path:
    return settings.media_processed_files_path.resolve()


def _in_processed(path: Path, settings: Settings) -> bool:
    return _processed_root(settings) in path.resolve().parents


def combined_source(record: m.MediaFile, *, dry_run: bool = False) -> Path:
    """The renamed copy. A dry run may read the original, since it writes nothing."""
    path = Path(record.processed_path) if record.processed_path else None
    if path is not None and path.exists():
        return path
    if dry_run and record.original_path and Path(record.original_path).exists():
        return Path(record.original_path)
    raise MamError("Run the Renamer first: the splitter works on the renamed copy")


def analyse(
    session: Session, record: m.MediaFile, audio: Path, settings: Settings
) -> tuple[Decision, bool, float]:
    """Transcribe (or reuse the transcript) and detect. Returns (decision, cached, duration)."""
    info = probe(audio)
    segments, cached = get_or_transcribe(
        session, audio, get_transcriber(settings), settings, content_sha=sha256(audio)
    )
    decision = detect(segments, info.duration, find_silences(audio, info.duration))
    return decision, cached, info.duration


def run_kirtan_splitter(ctx: JobContext) -> ToolOutcome:
    record, settings = ctx.media_file, ctx.settings
    if record is None:
        raise MamError("The kirtan splitter needs a file")
    src = combined_source(record, dry_run=ctx.dry_run)
    work_audio = src
    if record.kind == "video":
        # Video first (SPECS.md): the audio is saved as mp3 in its original quality.
        stem = (settings.work_dir / "split" if ctx.dry_run else src.parent) / src.stem
        work_audio = extract_audio(src, stem, dry_run=False)
    ctx.session.commit()  # transcription is slow; do not hold the database
    try:
        decision, cached, duration = analyse(ctx.session, record, work_audio, settings)
    finally:
        if ctx.dry_run and work_audio != src:
            work_audio.unlink(missing_ok=True)
    data = {**decision.to_dict(), "duration": duration, "transcript_cached": cached}
    summary = describe(decision)

    if ctx.dry_run:
        return ToolOutcome("info", f"Would: {summary}", data)

    meta = dict(record.meta or {})
    meta["splitter"] = {"proposal": data, "audio": str(work_audio)}
    record.meta = meta
    limit = threshold(ctx.session, settings)
    spot = random.random() < settings.spot_check_rate
    if decision.mode == "unclear" or decision.score < limit or spot:
        reason = "\n".join(decision.reasons)
        if spot and decision.score >= limit:
            reason = "Spot check: " + reason
        _open_review(ctx.session, record, data, reason, decision.score, spot)
        return ToolOutcome(
            "review", f"{summary} (score {decision.score:.2f}, needs a review)", data
        )
    if decision.mode in ("kirtan_only", "class_only"):
        return ToolOutcome("skipped", summary, data)
    result = apply_split(ctx.session, record, work_audio, data, settings, dry_run=False)
    return ToolOutcome("done", f"{summary} {result['summary']}", {**data, **result})


def describe(d: Decision) -> str:
    if d.mode == "split" and d.split_at is not None:
        return f"{d.mantra} until {format_time(d.split_at)}, then the class (score {d.score:.2f})."
    if d.mode == "kirtan_only":
        return "Singing throughout: kirtan recording, not split."
    if d.mode == "class_only":
        return "No kirtan before the class: not split."
    return "Could not decide: " + (d.reasons[0] if d.reasons else "unclear.")


def _open_review(
    session: Session,
    record: m.MediaFile,
    data: dict[str, Any],
    reason: str,
    score: float,
    spot_check: bool,
) -> None:
    for old in session.scalars(
        select(m.Review).where(
            m.Review.media_file_id == record.id,
            m.Review.tool == TOOL,
            m.Review.outcome == "pending",
        )
    ):
        old.outcome = "superseded"
        old.decided_at = datetime.now(UTC)
    session.add(
        m.Review(
            media_file_id=record.id,
            tool=TOOL,
            reason=reason,
            proposal=data,
            features=data.get("features"),
            score=score,
            spot_check=spot_check,
        )
    )


# --------------------------------------------------------------------- naming
def kirtan_name(parent_name: str, proposal: dict[str, Any] | None, mantra: str, ext: str) -> str:
    """The kirtan keeps the date and place of the recording; its WHAT is the mantra type."""
    from mam.tools.renamer.naming import build_name

    if proposal and proposal.get("when"):
        return build_name(proposal["when"], mantra, proposal.get("where"), ext)
    parts = Path(parent_name).stem.split("_")
    when = (
        parts[0]
        if parts and re.fullmatch(r"[\dYMD]{4}-[\dMD]{2}-[\dMD]{2}", parts[0])
        else "YYYY-MM-DD"
    )
    return build_name(when, mantra, None, ext)


# ----------------------------------------------------------------- the split
def apply_split(
    session: Session,
    record: m.MediaFile,
    audio: Path,
    decision: dict[str, Any],
    settings: Settings,
    *,
    dry_run: bool,
    operator: str | None = None,
) -> dict[str, Any]:
    """Cut `audio` at `decision['split_at']` into a kirtan file and the class file.

    The kirtan goes to its category folder; the class replaces the combined processed copy
    (which is a copy, never the original) and keeps its row. The kirtan gets a new row.
    """
    split_at = float(decision["split_at"])
    start = float(decision.get("kirtan_start") or 0.0)
    mantra = decision.get("mantra") or phrases.MAHA_MANTRA
    if not (start < split_at):
        raise MamError("The kirtan would be empty: the cut must be after its start")
    if not _in_processed(audio, settings):
        raise MamError(f"Refusing to cut {audio}: it is not a processed copy")
    duration = probe(audio).duration
    if split_at >= duration - 1:
        raise MamError("The cut is at the end of the recording; there is no class after it")

    ext = audio.suffix
    proposal = (record.meta or {}).get("proposal")
    new_kirtan = kirtan_name(audio.name, proposal, mantra, ext)
    from mam.tools.renamer.knowledge import get_reference

    ref = get_reference()
    organizer = FileOrganizer(ref.matcher, settings)
    folder, match = organizer.target_folder(mantra)
    kirtan_dst = _free_name(folder, new_kirtan)
    class_dst = audio.with_suffix(ext)  # the class takes the place (and name) of the combined copy
    video = record.kind == "video"
    assert_writable(kirtan_dst, settings)

    cmds = stream_cut(audio, kirtan_dst, [(start, split_at)], dry_run=dry_run)
    result: dict[str, Any] = {
        "kirtan_file": kirtan_dst.name,
        "class_file": class_dst.name,
        "commands": cmds,
    }
    if dry_run:
        result["summary"] = f"Would write {kirtan_dst.name} and {class_dst.name}."
        return result
    stream_cut(audio, class_dst, [(split_at, None)], dry_run=False)

    record_event(
        session,
        action="split",
        tool=TOOL,
        dry_run=False,
        media_file_id=record.id,
        src=audio,
        dst=kirtan_dst,
        detail={"split_at": split_at, "kirtan_start": start, "operator": operator},
    )
    kirtan = register_derived(session, record, kirtan_dst, "kirtan")
    kirtan.rec_date = record.rec_date
    kirtan.title = mantra
    kirtan.location = record.location
    kirtan.category = match.category.name if match else None
    kirtan.sha256 = sha256(kirtan_dst)
    set_status(
        session, kirtan, "Processing complete", TOOL, "kirtan file; later steps do not apply"
    )
    # The combined row becomes the class row.
    stat = class_dst.stat()
    class_id = record.id
    if video:  # the video copy stays as it is; the class audio becomes its own row
        class_rec = register_derived(session, record, class_dst, "class")
        class_rec.rec_date, class_rec.title = record.rec_date, record.title
        class_rec.location, class_rec.category = record.location, record.category
        class_rec.sha256 = sha256(class_dst)
        set_status(session, class_rec, "Kirtan splitter Done", TOOL, "class audio of a video")
        class_id = class_rec.id
    else:
        record.size, record.mtime = stat.st_size, stat.st_mtime
        record.fingerprint = fingerprint(class_dst)
        record.sha256 = sha256(class_dst)
        record.current_name = class_dst.name
        record.processed_path = str(class_dst)
    meta = dict(record.meta or {})
    meta["split"] = {
        "kirtan_id": kirtan.id,
        "class_id": class_id,
        "kirtan_file": kirtan_dst.name,
        "kirtan_start": start,
        "split_at": split_at,
        "mantra": mantra,
        "operator": operator,
    }
    record.meta = meta
    # Baserow writes log through their own database session: release ours first (SQLite lock).
    session.commit()
    baserow = _write_kirtan_row(record, kirtan, kirtan_dst, mantra, proposal, settings)
    result.update(kirtan_id=kirtan.id, baserow=baserow)
    result["summary"] = f"Wrote {kirtan_dst.name} and kept {class_dst.name} as the class."
    return result


def _free_name(folder: Path, name: str) -> Path:
    dst = folder / name
    n = 2
    while dst.exists():
        stem, dot, ext = name.rpartition(".")
        dst = folder / f"{stem}-{n}.{ext}"
        n += 1
    return dst


def _write_kirtan_row(
    record: m.MediaFile,
    kirtan: m.MediaFile,
    path: Path,
    mantra: str,
    proposal: dict[str, Any] | None,
    settings: Settings,
) -> dict[str, Any] | None:
    """New Baserow row for the kirtan (the class keeps the original row)."""
    if not proposal:
        return None
    from mam.tools.renamer.tool import write_baserow

    row = dict(proposal)
    row.update(
        what=mantra,
        title=mantra,
        verse_tag=None,
        baserow_row_id=None,
        notes_text=None,
        category=kirtan.category,
        original_path=record.original_path or str(path),
    )
    result = write_baserow(kirtan, row, path, settings, dry_run=False)
    if result.get("row_id"):
        kirtan.baserow_row_id = result["row_id"]
    return result


# ------------------------------------------------------------------- reviews
def decide_review(
    session: Session,
    review: m.Review,
    *,
    action: str,
    split_at: str | None,
    mantra: str | None,
    operator: str,
    note: str | None,
) -> str:
    """Approve with the (possibly edited) cut, or as 'do not split'. Returns the message."""
    settings = get_settings()
    record = session.get(m.MediaFile, review.media_file_id)
    proposal = review.proposal
    audio = Path((record.meta or {}).get("splitter", {}).get("audio") or combined_source(record))
    if action == "no_split":
        review.outcome = "edited" if proposal.get("mode") == "split" else "approved"
        review.final = {"mode": "no_split"}
        review.operator, review.note, review.decided_at = operator, note, datetime.now(UTC)
        set_status(session, record, "Skipped", TOOL, f"not split (approved by {operator})")
        return "Approved: the recording is not split"
    if not split_at:
        raise MamError("Enter the time where the kirtan ends")
    cut = parse_time(split_at)
    decision = dict(proposal)
    decision.update(mode="split", split_at=cut, mantra=mantra or proposal.get("mantra"))
    if proposal.get("split_at") is None or proposal.get("mode") != "split":
        decision["kirtan_start"] = float(proposal.get("kirtan_start") or 0.0)
    result = apply_split(
        session, record, audio, decision, settings, dry_run=False, operator=operator
    )
    moved = proposal.get("split_at") is None or abs(cut - float(proposal["split_at"])) > TOLERANCE_S
    review.outcome = (
        "edited" if moved or decision["mantra"] != proposal.get("mantra") else "approved"
    )
    review.final = {"mode": "split", "split_at": cut, "mantra": decision["mantra"]}
    review.operator, review.note, review.decided_at = operator, note, datetime.now(UTC)
    set_status(session, record, "Kirtan splitter Done", TOOL, f"split at {format_time(cut)}")
    return f"Approved and split at {format_time(cut)}: {result['summary']}"
