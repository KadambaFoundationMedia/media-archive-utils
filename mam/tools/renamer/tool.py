"""Renamer job: propose, copy into processed-files, record evidence, flag or write Baserow."""

from __future__ import annotations

import logging
import re
import threading
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.orm import Session

from mam.config import Settings, get_settings
from mam.errors import MamError
from mam.helpers.file_organizer import FileOrganizer
from mam.helpers.local_db import models as m
from mam.helpers.local_db.app_settings import baserow_writes_enabled
from mam.helpers.local_db.db import session_scope
from mam.helpers.local_db.repo import set_status
from mam.manager.tools import JobContext, ToolOutcome
from mam.tools.renamer.dates import DateValue
from mam.tools.renamer.knowledge import Reference, get_reference, norm_key
from mam.tools.renamer.naming import build_name, format_where, problems
from mam.tools.renamer.resolve import Proposal, Resolver

log = logging.getLogger(__name__)
TOOL = "renamer"
ALIAS_ACTIVATION = 3  # a correction made this many times becomes a rule (SPEC.md section 10)


def resolver(settings: Settings | None = None) -> Resolver:
    settings = settings or get_settings()
    return Resolver(get_reference(), [p for _, p in settings.import_roots])


def source_path(record: m.MediaFile) -> Path:
    """The file to rename: the processed copy if there is one, otherwise the original."""
    if record.processed_path and Path(record.processed_path).exists():
        return Path(record.processed_path)
    return Path(record.original_path)


def archive_path(path: Path, settings: Settings) -> str:
    try:
        return (
            "/"
            + path.resolve().relative_to(settings.media_processed_files_path.resolve()).as_posix()
        )
    except ValueError:
        return str(path)


# ------------------------------------------------------------------ Baserow
def baserow_values(
    proposal: dict[str, Any], final_path: Path, kind: str, ref: Reference, settings: Settings
) -> dict[str, Any]:
    values: dict[str, Any] = {
        "title": proposal["title"],
        "tag": proposal.get("verse_tag"),
        "language": [lang for lang in proposal["languages"] if lang in ref.language_options],
        ("video_file_location" if kind == "video" else "audio_file_location"): archive_path(
            final_path, settings
        ),
        "notes": f"Renamer: {Path(proposal['original_path']).name} -> {final_path.name}",
    }
    when = DateValue(*_date_parts(proposal["when"]))
    if when.is_full:
        values["date"] = when.text()
    if proposal.get("category") in ref.category_options:
        values["category"] = proposal["category"]
    if proposal.get("place_name") in ref.place_options:
        values["place_location"] = proposal["place_name"]
    country = ref.country_option(proposal.get("cc"))
    if country:
        values["country"] = country
    return {k: v for k, v in values.items() if v not in (None, "", [])}


def _date_parts(text: str) -> tuple[int | None, int | None, int | None]:
    parts = text.split("-")
    return tuple(int(p) if p.isdigit() else None for p in parts)  # type: ignore[return-value]


def write_baserow(
    record: m.MediaFile,
    proposal: dict[str, Any],
    final_path: Path,
    settings: Settings,
    dry_run: bool,
    live: bool = False,
) -> dict[str, Any]:
    """Create or update the media row. Runs follow the "Baserow writes" switch; `live` (a
    reviewer's approval) always writes for real."""
    from mam.helpers.baserow import BaserowClient

    ref = get_reference()
    values = baserow_values(proposal, final_path, record.kind, ref, settings)
    with BaserowClient(settings) as client:
        apply = (baserow_writes_enabled() or live) and not dry_run
        row_id = proposal.get("baserow_row_id") or record.baserow_row_id
        if row_id:
            result = client.update_row(
                client.media_table,
                row_id,
                values,
                tool=TOOL,
                media_file_id=record.id,
                dry_run=not apply,
            )
            action = "update"
        else:
            values.update(
                {
                    "status_media": "Not-started",
                    "status_thumb": "Not-started",
                    "status_transcript": "Not-started",
                    "imported_on": date.today().isoformat(),
                }
            )
            result = client.create_row(
                client.media_table, values, tool=TOOL, media_file_id=record.id, dry_run=not apply
            )
            action = "create"
    return {
        "action": action,
        "row_id": result.row_id,
        "dry_run": result.dry_run,
        "conflicts": {k: list(v) for k, v in (result.conflicts or {}).items()},
    }


# ------------------------------------------------------------- unique names
_names_lock = threading.Lock()


def _with_suffix(name: str, suffix: str) -> str:
    stem, dot, ext = name.rpartition(".")
    return f"{stem}{suffix}.{ext}" if dot else f"{name}{suffix}"


def reserve_name(record_id: int, folder: Path, name: str) -> tuple[str, str | None]:
    """Give `name` to this recording, or the next free `-2`, `-3`, ... in `folder`.

    Names are taken by other recordings' processed copies and by their (dry-run) proposals,
    so two different recordings never get the same proposed name. The reservation is saved
    at once, so parallel jobs see it. Returns (name, original path of the recording that
    already had the name, if any).
    """
    with _names_lock, session_scope() as s:
        prefix = f"{folder}/"
        target = func.json_extract(m.MediaFile.meta, "$.reserved")
        rows = s.execute(
            select(m.MediaFile.original_path, m.MediaFile.processed_path, target).where(
                m.MediaFile.id != record_id,
                or_(m.MediaFile.processed_path.like(f"{prefix}%"), target.like(f"{prefix}%")),
            )
        ).all()
        taken: dict[str, str] = {}
        for original, processed, reserved in rows:
            for path in (processed, reserved):
                if path and Path(path).parent == folder:
                    taken.setdefault(Path(path).name.lower(), original)
        chosen, n = name, 2
        while chosen.lower() in taken:
            chosen = _with_suffix(name, f"-{n}")
            n += 1
        s.execute(
            update(m.MediaFile)
            .where(m.MediaFile.id == record_id)
            .values(
                meta=func.json_set(
                    func.coalesce(m.MediaFile.meta, "{}"), "$.reserved", str(folder / chosen)
                )
            )
        )
    return chosen, taken.get(name.lower()) if chosen != name else None


def latest_approval(session: Session, file_id: int) -> m.Review | None:
    """The reviewer's decision for this file, if its latest decided review approved a name."""
    review = session.scalar(
        select(m.Review)
        .where(
            m.Review.media_file_id == file_id,
            m.Review.tool == TOOL,
            m.Review.outcome.in_(("approved", "edited", "rejected", "excluded")),
        )
        .order_by(m.Review.decided_at.desc(), m.Review.id.desc())
    )
    return review if review is not None and review.outcome in ("approved", "edited") else None


def baserow_summary(baserow: dict | None, flagged: bool = False) -> str:
    """One line for the job result: what happened in Baserow."""
    if flagged or not baserow:
        return "Baserow: written when you approve the review"
    what = "new row" if baserow.get("action") == "create" else "row update"
    if baserow.get("dry_run"):
        return f"Baserow: {what} logged only (Baserow writes are off)"
    row = baserow.get("row_id")
    return f"Baserow: {what} written (row {row})" if row else f"Baserow: {what} written"


# ------------------------------------------------------------------- the job
def run_renamer(ctx: JobContext) -> ToolOutcome:
    record = ctx.media_file
    if record is None or not record.original_path:
        raise MamError("The Renamer needs an original file")
    settings = ctx.settings
    approved = latest_approval(ctx.session, record.id)
    if approved is not None and not ctx.params.get("override_review"):
        # A reviewer's decision is never overwritten by a new automatic run.
        return ToolOutcome(
            "info",
            f"Kept {approved.final.get('final_name')}, approved by {approved.operator} "
            f"on {approved.decided_at:%Y-%m-%d}",
            {"kept": approved.final.get("final_name")},
        )
    res = resolver(settings)
    proposal = res.propose(Path(record.original_path))

    # Release the session before helpers write through their own sessions (SQLite locking).
    ctx.session.commit()
    organizer = FileOrganizer(res.ref.matcher, settings)
    folder, _ = organizer.target_folder(proposal.what, proposal.category)
    unique, other = reserve_name(record.id, folder, proposal.new_name)
    if other:
        proposal.flags.append(
            f"Same name as another recording ({Path(other).name}); named {unique} instead. "
            "Add a title or date that tells them apart."
        )
        proposal.new_name = unique
    placed = organizer.organize(
        source_path(record),
        proposal.new_name,
        proposal.what,
        tool=TOOL,
        dry_run=ctx.dry_run,
        media_file_id=record.id,
        category=proposal.category,
    )
    if placed.clash:
        proposal.flags.append(
            f"Another file already has the name {proposal.new_name}; saved as {placed.dst.name}."
        )
    if placed.duplicate:
        proposal.notes.append(
            f"Same name and size as an existing file; saved as {placed.dst.name}."
        )
    data = proposal.to_dict(dry_run=ctx.dry_run, final_name=placed.dst.name, target=str(placed.dst))

    if not ctx.dry_run:
        # Persist the new location at once, so a retry after a Baserow error renames in place.
        with session_scope() as s:
            s.execute(
                update(m.MediaFile)
                .where(m.MediaFile.id == record.id)
                .values(processed_path=str(placed.dst), current_name=placed.dst.name)
            )
        if not proposal.flags:
            data["baserow"] = write_baserow(record, data, placed.dst, settings, dry_run=False)
    else:
        data["baserow"] = {
            "action": "update" if proposal.baserow_row_id else "create",
            "dry_run": True,
        }

    meta = dict(record.meta or {})
    meta["proposal"] = data
    meta["reserved"] = str(placed.dst)
    record.meta = meta
    summary = f"{'Would rename' if ctx.dry_run else 'Renamed'} to {placed.dst.name}"
    if proposal.flags and ctx.dry_run:
        summary += (
            f" — would need a review ({len(proposal.flags)} item(s)); "
            "run without dry run to open it"
        )
    elif proposal.flags:
        summary += f" — {len(proposal.flags)} item(s) to review"
    if ctx.dry_run:
        return ToolOutcome("info", summary, {"new_name": placed.dst.name, "flags": proposal.flags})
    summary += " — " + baserow_summary(data.get("baserow"), flagged=bool(proposal.flags))

    _store_evidence(ctx.session, record, proposal)
    _apply_display(record, data, placed.dst)
    if data.get("baserow", {}).get("row_id"):
        record.baserow_row_id = data["baserow"]["row_id"]
    if proposal.flags:
        _open_review(ctx.session, record, data, proposal)
        return ToolOutcome(
            "review", summary, {"new_name": placed.dst.name, "flags": proposal.flags}
        )
    return ToolOutcome("done", summary, {"new_name": placed.dst.name})


def _store_evidence(session: Session, record: m.MediaFile, proposal: Proposal) -> None:
    session.execute(
        delete(m.WEvidence).where(m.WEvidence.media_file_id == record.id, m.WEvidence.tool == TOOL)
    )
    for e in proposal.evidence:
        session.add(
            m.WEvidence(
                media_file_id=record.id,
                field=e.field,
                value=e.value,
                source=e.source,
                score=e.score,
                tool=TOOL,
                selected=e.selected,
                detail=e.detail,
            )
        )


def _apply_display(record: m.MediaFile, data: dict[str, Any], final: Path) -> None:
    record.processed_path = str(final)
    record.current_name = final.name
    record.rec_date = data["when"]
    record.title = data.get("what")
    record.location = data.get("where")
    record.category = data.get("category")


def _open_review(session: Session, record: m.MediaFile, data: dict[str, Any], proposal) -> None:
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
            reason="\n".join(proposal.flags),
            proposal=data,
            features={"evidence": [e.__dict__ for e in proposal.evidence]},
        )
    )


# ------------------------------------------------------------------ reviews
WHEN_RE = re.compile(r"^(\d{4}|YYYY)-(\d{2}|MM)-(\d{2}|DD)$")


def valid_when(text: str) -> bool:
    match = WHEN_RE.match(text)
    if not match:
        return False
    y, mo, d = (int(g) if g.isdigit() else None for g in match.groups())
    if (mo is not None and not 1 <= mo <= 12) or (d is not None and not 1 <= d <= 31):
        return False
    if None not in (y, mo, d):
        try:
            date(y, mo, d)
        except ValueError:
            return False
    return True


def preview(values: dict[str, str], ext: str) -> dict[str, Any]:
    """Filename for edited review values, with convention problems and the target folder."""
    when = (values.get("when") or "YYYY-MM-DD").strip() or "YYYY-MM-DD"
    what = (values.get("what") or "").strip() or None
    ref = get_reference()
    place_text = (values.get("place") or "").strip() or None
    cc = (values.get("cc") or "").strip().lower() or None
    place = ref.find_place(place_text) if place_text else None
    if place:
        place_text = place.display  # 'ISKCON-Ljubljana' or 'ljubljana' -> 'Ljubljana'
        cc = cc or place.cc  # a known place brings its country
    where = format_where(place_text, cc)
    name = build_name(when, what, where, ext)
    issues = problems(name)
    if not valid_when(when):
        issues.insert(0, "date must be YYYY-MM-DD (YYYY, MM and DD may be placeholders)")
    match = ref.matcher.match(what)
    category = match.category.name if match else values.get("category") or None
    return {"name": name, "problems": issues, "category": category, "where": where, "cc": cc}


def approve_review(
    session: Session, review: m.Review, values: dict[str, str], operator: str
) -> m.MediaFile:
    record = session.get(m.MediaFile, review.media_file_id)
    proposal = dict(review.proposal)
    result = preview(values, proposal["ext"])
    if result["problems"]:
        raise MamError("; ".join(result["problems"]))
    settings = get_settings()
    ref = get_reference()
    session.commit()  # release before helpers write
    organizer = FileOrganizer(ref.matcher, settings)
    folder, _ = organizer.target_folder(values.get("what") or None, result["category"])
    name, _ = reserve_name(record.id, folder, result["name"])
    placed = organizer.organize(
        source_path(record),
        name,
        values.get("what") or None,
        tool="renamer-review",
        dry_run=False,
        media_file_id=record.id,
        category=result["category"],
    )
    place_text = (values.get("place") or "").strip() or None
    place = ref.find_place(place_text) if place_text else None
    approved = {
        **proposal,
        "when": values.get("when") or "YYYY-MM-DD",
        "what": values.get("what") or None,
        "place": place_text,
        "place_name": place.name if place else None,
        "cc": result["cc"],
        "where": result["where"],
        "category": result["category"],
        "title": values.get("title") or proposal["title"],
        "baserow_row_id": int(values["baserow_row_id"]) if values.get("baserow_row_id") else None,
        "final_name": placed.dst.name,
        "target": str(placed.dst),
        "dry_run": False,
    }
    # Approving is an explicit decision: it writes to Baserow even when the switch is off.
    approved["baserow"] = write_baserow(
        record, approved, placed.dst, settings, dry_run=False, live=True
    )

    edited = any(
        str(proposal.get(k) or "") != str(approved.get(k) or "")
        for k in ("when", "what", "place", "cc")
    )
    _learn_alias(session, proposal.get("place"), place_text)
    meta = dict(record.meta or {})
    meta["proposal"] = approved
    meta["reserved"] = str(placed.dst)
    record.meta = meta
    _apply_display(record, approved, placed.dst)
    if approved["baserow"].get("row_id"):
        record.baserow_row_id = approved["baserow"]["row_id"]
    review.outcome = "edited" if edited else "approved"
    review.final = approved
    review.operator = operator
    review.decided_at = datetime.now(UTC)
    set_status(session, record, "Renamer Done", "renamer-review", f"{review.outcome} by {operator}")
    return record


def _learn_alias(session: Session, old: str | None, new: str | None) -> None:
    """Count a reviewer's place correction; after ALIAS_ACTIVATION times it becomes a rule."""
    if not old or not new or norm_key(old) == norm_key(new):
        return
    token = norm_key(old)
    rule = session.scalar(
        select(m.AliasRule).where(
            m.AliasRule.field == "where", m.AliasRule.token == token, m.AliasRule.replacement == new
        )
    )
    if rule is None:
        rule = m.AliasRule(field="where", token=token, replacement=new, occurrences=0)
        session.add(rule)
    rule.occurrences += 1
    rule.active = rule.occurrences >= ALIAS_ACTIVATION


def reject_review(session: Session, review: m.Review, operator: str, note: str | None) -> None:
    """Discard the Renamer result: remove the processed copy and return to Not processed."""
    record = session.get(m.MediaFile, review.media_file_id)
    settings = get_settings()
    copy = Path(record.processed_path) if record.processed_path else None
    root = settings.media_processed_files_path.resolve()
    if copy and copy.exists() and root in copy.resolve().parents:
        copy.unlink()  # a processed copy, never an original
    record.processed_path = None
    record.current_name = None
    record.meta = {k: v for k, v in (record.meta or {}).items() if k != "reserved"}
    record.rec_date = record.title = record.location = record.category = None
    review.outcome, review.operator, review.note = "rejected", operator, note
    review.decided_at = datetime.now(UTC)
    set_status(session, record, "Not processed", "renamer-review", f"rejected by {operator}")


def exclude_file(session: Session, review: m.Review, operator: str, note: str | None) -> None:
    record = session.get(m.MediaFile, review.media_file_id)
    review.outcome, review.operator, review.note = "excluded", operator, note
    review.decided_at = datetime.now(UTC)
    set_status(session, record, "Excluded", "renamer-review", note or f"excluded by {operator}")
