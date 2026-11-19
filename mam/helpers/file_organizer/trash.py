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

    trash_processed_copy(session, record, tool="media-manager", settings=settings)
    for review in session.scalars(
        select(m.Review).where(m.Review.media_file_id == record.id, m.Review.outcome == "pending")
    ):
        review.outcome = "deleted"
        review.operator = settings.operator_name
    set_status(session, record, DELETED, "media-manager", f"deleted by {settings.operator_name}")
    log.info("Deleted file %s (%s)", record.id, record.current_name)
    return DeleteResult(True, trashed, "Moved to the Trash" if trashed else "Marked as deleted")
