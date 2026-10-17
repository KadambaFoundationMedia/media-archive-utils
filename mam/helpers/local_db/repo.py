"""Operations other tools use to read and update file state."""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from mam.config import get_settings
from mam.helpers.local_db import models as m
from mam.helpers.local_db.hashing import fingerprint, sha256

AUDIO_EXTENSIONS = {
    ".mp3",
    ".wma",
    ".m4a",
    ".wav",
    ".flac",
    ".aac",
    ".ogg",
    ".opus",
    ".aif",
    ".aiff",
}
VIDEO_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".avi",
    ".mkv",
    ".mpg",
    ".mpeg",
    ".wmv",
    ".m4v",
    ".mts",
    ".vob",
    ".flv",
}


def media_kind(path: Path) -> str | None:
    ext = path.suffix.lower()
    if ext in AUDIO_EXTENSIONS:
        return "audio"
    if ext in VIDEO_EXTENSIONS:
        return "video"
    return None


def register_original(session: Session, path: Path) -> m.MediaFile | None:
    """Register an original media file, or return the existing record.

    Byte-identical files found at another path are linked as aliases.
    Returns None for files that are not audio or video (they are ignored).
    """
    path = path.resolve()
    kind = media_kind(path)
    if kind is None:
        return None
    existing = session.scalar(select(m.MediaFile).where(m.MediaFile.original_path == str(path)))
    if existing:
        return existing
    alias = session.scalar(select(m.FileAlias).where(m.FileAlias.path == str(path)))
    if alias:
        return alias.media_file

    stat = path.stat()
    fp = fingerprint(path)
    for candidate in session.scalars(
        select(m.MediaFile).where(m.MediaFile.fingerprint == fp, m.MediaFile.parent_id.is_(None))
    ):
        # Same fingerprint: confirm with a full hash before treating as identical.
        if ensure_sha256(session, candidate) == sha256(path):
            session.add(m.FileAlias(media_file_id=candidate.id, path=str(path)))
            return candidate

    record = m.MediaFile(
        fingerprint=fp, size=stat.st_size, mtime=stat.st_mtime, kind=kind, original_path=str(path)
    )
    session.add(record)
    session.flush()
    return record


def ensure_sha256(session: Session, record: m.MediaFile) -> str:
    if not record.sha256:
        source = record.original_path or record.processed_path
        if source is None:
            raise ValueError(f"media_file {record.id} has no path to hash")
        record.sha256 = sha256(Path(source))
        session.flush()
    return record.sha256


def register_derived(
    session: Session, parent: m.MediaFile, path: Path, derivation: str
) -> m.MediaFile:
    """Register a file produced by a tool (split, trim, optimize) with lineage to its parent."""
    path = path.resolve()
    stat = path.stat()
    record = m.MediaFile(
        fingerprint=fingerprint(path),
        size=stat.st_size,
        mtime=stat.st_mtime,
        kind=media_kind(path) or parent.kind,
        processed_path=str(path),
        current_name=path.name,
        parent_id=parent.id,
        derivation=derivation,
        baserow_row_id=None,
    )
    session.add(record)
    session.flush()
    return record


def set_status(
    session: Session,
    record: m.MediaFile,
    status: str,
    tool: str | None = None,
    note: str | None = None,
) -> None:
    if record.status == status:
        return
    session.add(
        m.StatusHistory(
            media_file_id=record.id,
            old_status=record.status,
            new_status=status,
            tool=tool,
            note=note,
        )
    )
    record.status = status


def record_event(
    session: Session,
    *,
    action: str,
    tool: str,
    dry_run: bool,
    media_file_id: int | None = None,
    src: Path | str | None = None,
    dst: Path | str | None = None,
    detail: dict[str, Any] | None = None,
) -> m.FileEvent:
    event = m.FileEvent(
        media_file_id=media_file_id,
        action=action,
        tool=tool,
        src=str(src) if src else None,
        dst=str(dst) if dst else None,
        dry_run=dry_run,
        detail=detail,
        operator=get_settings().operator_name,
    )
    session.add(event)
    session.flush()
    return event


def export_db(engine: Engine, out_dir: Path) -> Path:
    """Write a consistent copy of the SQLite file plus one JSON file per table."""
    target = out_dir / f"mam-export-{datetime.now():%Y%m%d-%H%M%S}"
    target.mkdir(parents=True)
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.exec_driver_sql("VACUUM INTO ?", (str(target / "mam.sqlite"),))
        for table in m.Base.metadata.sorted_tables:
            rows = [dict(r._mapping) for r in conn.execute(table.select())]
            (target / f"{table.name}.json").write_text(
                json.dumps(rows, default=str, ensure_ascii=False, indent=1), encoding="utf-8"
            )
    return target


def import_db(source: Path, db_path: Path) -> Path | None:
    """Replace the local DB with an exported SQLite file. The current DB is kept as a backup."""
    sqlite_file = source / "mam.sqlite" if source.is_dir() else source
    backup = None
    if db_path.exists():
        backup = db_path.with_suffix(f".backup-{datetime.now():%Y%m%d-%H%M%S}.sqlite")
        shutil.copy2(db_path, backup)
    shutil.copy2(sqlite_file, db_path)
    return backup
