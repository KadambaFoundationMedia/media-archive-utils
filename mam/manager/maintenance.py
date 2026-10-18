"""Reset the media manager to a clean start (Settings page and `mam reset`).

Originals are never touched: only the local database and the copies in processed-files are
removed. A full backup of the database is made first. Baserow is not changed.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path

from sqlalchemy import delete, func, select

from mam.config import Settings, get_settings
from mam.errors import MamError
from mam.helpers.local_db import models as m
from mam.helpers.local_db.db import get_engine, session_scope
from mam.helpers.local_db.repo import export_db

log = logging.getLogger(__name__)

# What a reset can clear, beyond the always-cleared processing data.
LEARNED_TABLES = (m.AliasRule, m.Calibration, m.VoiceProfile)
CACHE_TABLES = (m.VedabaseCache, m.LlmCache, m.TranscriptSegment, m.TranscriptRun)
KEEP_FILES = {".gitkeep"}


@dataclass
class ResetOptions:
    processed_files: bool = True
    learned: bool = False  # alias rules, calibration, voice profile
    caches: bool = False  # vedabase, LLM, transcripts, Baserow reference data


@dataclass
class ResetResult:
    backup: str
    rows_deleted: dict[str, int] = field(default_factory=dict)
    files_deleted: int = 0
    folders_deleted: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def summary(settings: Settings | None = None) -> dict:
    """What a reset would remove, for the Settings page."""
    settings = settings or get_settings()
    with session_scope() as s:

        def count(model) -> int:
            return s.scalar(select(func.count()).select_from(model)) or 0

        data = {
            "files": count(m.MediaFile),
            "jobs": count(m.Job),
            "reviews": count(m.Review),
            "baserow_log": count(m.BaserowWriteLog),
            "baserow_live": s.scalar(
                select(func.count())
                .select_from(m.BaserowWriteLog)
                .where(m.BaserowWriteLog.dry_run.is_(False), m.BaserowWriteLog.undone.is_(False))
            )
            or 0,
            "learned": sum(count(t) for t in LEARNED_TABLES),
            "cached": sum(count(t) for t in CACHE_TABLES),
            "active_jobs": s.scalar(
                select(func.count())
                .select_from(m.Job)
                .where(m.Job.state.in_(("queued", "running")))
            )
            or 0,
        }
    files, size = 0, 0
    root = settings.media_processed_files_path
    if root.is_dir():
        for path in root.rglob("*"):
            if path.is_file() and path.name not in KEEP_FILES:
                files += 1
                size += path.stat().st_size
    data["processed_files"] = files
    data["processed_bytes"] = size
    return data


def _check_processed_root(settings: Settings) -> Path:
    root = settings.media_processed_files_path.resolve()
    for original in settings.read_only_roots:
        if root == original or original in root.parents or root in original.parents:
            raise MamError(
                f"Refusing to clear {root}: it overlaps an originals folder ({original})"
            )
    if root == Path(root.anchor) or root == Path.home().resolve():
        raise MamError(f"Refusing to clear {root}")
    return root


def reset(options: ResetOptions, settings: Settings | None = None) -> ResetResult:
    settings = settings or get_settings()
    root = _check_processed_root(settings) if options.processed_files else None
    with session_scope() as s:
        active = s.scalar(
            select(func.count()).select_from(m.Job).where(m.Job.state.in_(("queued", "running")))
        )
    if active:
        raise MamError(f"{active} job(s) are queued or running. Cancel them or wait, then reset.")

    backup_dir = settings.local_db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = export_db(get_engine(), backup_dir)
    result = ResetResult(backup=str(backup))

    keep = set()
    if not options.learned:
        keep |= {t.__tablename__ for t in LEARNED_TABLES}
    if not options.caches:
        keep |= {t.__tablename__ for t in CACHE_TABLES}
    with session_scope() as s:
        for table in reversed(m.Base.metadata.sorted_tables):  # children before parents
            if table.name in keep:
                continue
            outcome = s.execute(delete(table))
            result.rows_deleted[table.name] = outcome.rowcount or 0
    with get_engine().connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.exec_driver_sql("VACUUM")

    if options.caches:
        cache_file = settings.local_db_path.parent / "cache" / "baserow_reference.json"
        cache_file.unlink(missing_ok=True)
        from mam.tools.renamer.knowledge import reset_cache

        reset_cache()

    if root is not None and root.is_dir():
        for child in root.iterdir():
            if child.name in KEEP_FILES:
                continue
            if child.is_dir() and not child.is_symlink():
                result.files_deleted += sum(1 for p in child.rglob("*") if p.is_file())
                shutil.rmtree(child)
                result.folders_deleted += 1
            else:
                child.unlink()
                result.files_deleted += 1
    log.warning("Media manager reset: %s", result.to_dict())
    return result
