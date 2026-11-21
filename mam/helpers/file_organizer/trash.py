"""Delete a file from the portal: the processed copy goes to the OS Trash (SPEC.md section 2).

Originals are never deleted: only a copy below the processed-files root is moved to the Trash
(macOS Trash, Windows Recycle Bin), so it can be put back by hand. The local record stays, with
the status `Deleted`, so a later import or scan does not bring the file back for processing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from mam.config import Settings, get_settings
from mam.errors import MamError
from mam.helpers.local_db import models as m
from mam.helpers.local_db.repo import record_event, set_status

log = logging.getLogger(__name__)

DELETED = "Deleted"


@dataclass
class DeleteResult:
    deleted: bool
    trashed: Path | None
    message: str


def _send_to_trash(path: Path) -> None:
    from send2trash import send2trash

    send2trash(path)


def processed_copy(record: m.MediaFile, settings: Settings | None = None) -> Path | None:
    """The record's copy in processed-files if it exists on disk, else None.

    Raises ValueError for a path outside processed-files: only our own copy is ever removed.
    """
    settings = settings or get_settings()
    if not record.processed_path:
        return None
    path = Path(record.processed_path)
    if not path.resolve().is_relative_to(settings.media_processed_files_path.resolve()):
        raise ValueError(f"Refusing to remove a file outside processed-files: {path}")
    return path if path.exists() else None


def sole_copy(record: m.MediaFile) -> bool:
    """True when the Renamer moved the original, so the processed file is the only copy."""
    meta = record.meta or {}
    return bool(meta.get("original_moved")) and not Path(record.original_path or "").exists()


def moved_duplicates(record: m.MediaFile) -> list[dict]:
    """Identical copies the Renamer moved to duplicate_files/ that are still there."""
    found = (record.meta or {}).get("duplicates") or []
    return [d for d in found if d.get("moved") and Path(d["to"]).exists()]


def forget_original_changes(record: m.MediaFile) -> None:
    meta = dict(record.meta or {})
    for key in ("original_moved", "original_now", "duplicates"):
        meta.pop(key, None)
    record.meta = meta


def put_original_back(
    session: Session, record: m.MediaFile, *, tool: str, settings: Settings | None = None
) -> None:
    """Undo what the Renamer did to the original, so a rejected or excluded file is as imported.

    - A moved original: the processed file (the only copy) goes back to where it was imported,
      with a verified copy if that is another volume. It is never trashed.
    - An original renamed with the postfix gets its own name back.
    If the way back is blocked (the folder is gone, or another file has that name) the file stays
    where it is and a MamError says so; nothing is deleted.
    """
    from mam.helpers.file_organizer.organizer import _move

    original = Path(record.original_path or "")
    meta = record.meta or {}
    for dup in moved_duplicates(record):
        place, kept = Path(dup["from"]), Path(dup["to"])
        if place.exists() or not place.parent.is_dir():
            raise MamError(
                f"Cannot put the identical copy back at {place}: that place is taken or the "
                f"folder is gone. It stays at {kept}."
            )
        if not _move(kept, place):
            raise MamError(f"Put back at {place}, but could not remove {kept}")
        record_event(
            session,
            action="restore",
            tool=tool,
            dry_run=False,
            media_file_id=record.id,
            src=kept,
            dst=place,
            detail={"duplicate": True},
        )
    if sole_copy(record):
        path = processed_copy(record, settings)
        if path is None:
            raise MamError("The moved file is missing from processed-files; nothing to put back")
        if original.exists() or not original.parent.is_dir():
            raise MamError(
                f"Cannot put the file back at {original}: that place is taken or the folder is "
                "gone. It stays in processed-files."
            )
        if not _move(path, original):
            raise MamError(f"Put back at {original}, but could not remove {path}")
        record_event(
            session,
            action="restore",
            tool=tool,
            dry_run=False,
            media_file_id=record.id,
            src=path,
            dst=original,
        )
        record.processed_path = None
    elif meta.get("original_now"):
        marked = Path(meta["original_now"])
        if marked.exists() and not original.exists():
            marked.rename(original)
            record_event(
                session,
                action="unmark_original",
                tool=tool,
                dry_run=False,
                media_file_id=record.id,
                src=marked,
                dst=original,
            )
    forget_original_changes(record)


def trash_processed_copy(
    session: Session, record: m.MediaFile, *, tool: str, settings: Settings | None = None
) -> Path | None:
    """Move the processed copy to the Trash, record a `delete` event and forget the path."""
    path = processed_copy(record, settings)
    if path is not None:
        _send_to_trash(path)
    record_event(
        session,
        action="delete",
        tool=tool,
        dry_run=False,
        media_file_id=record.id,
        src=record.processed_path,
        detail={"to": "trash" if path else "none (no processed copy on disk)"},
    )
    record.processed_path = None
    return path


def delete_file(
    session: Session,
    record: m.MediaFile,
    *,
    dry_run: bool = True,
    settings: Settings | None = None,
) -> DeleteResult:
    """Move the processed copy of `record` to the Trash and mark the record `Deleted`."""
    settings = settings or get_settings()
    if record.status == DELETED:
        return DeleteResult(False, None, "Already deleted")
    busy = session.scalar(
        select(m.Job.id).where(
            m.Job.media_file_id == record.id, m.Job.state.in_(("queued", "running"))
        )
    )
    if busy:
        return DeleteResult(False, None, "A job for this file is queued or running")

    try:
        trashed = processed_copy(record, settings)
    except ValueError as error:
        return DeleteResult(False, None, str(error))
    if dry_run:
        return DeleteResult(False, trashed, "Dry run: nothing deleted")

    was_sole_copy = sole_copy(record)
    trash_processed_copy(session, record, tool="media-manager", settings=settings)
    for review in session.scalars(
        select(m.Review).where(m.Review.media_file_id == record.id, m.Review.outcome == "pending")
    ):
        review.outcome = "deleted"
        review.operator = settings.operator_name
    set_status(session, record, DELETED, "media-manager", f"deleted by {settings.operator_name}")
    log.info("Deleted file %s (%s)", record.id, record.current_name)
    message = "Moved to the Trash" if trashed else "Marked as deleted"
    if trashed and was_sole_copy:
        message += " (the Renamer had moved the original, so this was the only copy)"
    return DeleteResult(True, trashed, message)
