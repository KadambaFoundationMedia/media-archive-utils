"""Register original media files found below a folder."""

from __future__ import annotations

import contextlib
import logging
import os
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from mam.helpers.local_db.hashing import fingerprint
from mam.helpers.local_db.repo import media_kind, register_original

log = logging.getLogger(__name__)


@dataclass
class ScanResult:
    root: str
    found: int = 0
    new: int = 0
    known: int = 0
    duplicates: int = 0
    ignored: int = 0
    errors: int = 0
    # Detail lists for the Jobs page (counts above are always exact; the lists are capped).
    ignored_by_type: list[dict] = field(default_factory=list)  # [{ext, count, files: [rel path]}]
    duplicates_by_folder: list[dict] = field(
        default_factory=list
    )  # [{folder, items: [{name, of}]}]
    details_truncated: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


# The detail lists are stored in the job result, so they are capped (thousands of files).
MAX_LISTED = 2000


def _rel(path: Path, root: Path) -> str:
    base = root.parent if root.is_file() else root
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        return path.as_posix()


def iter_media(root: Path):
    if root.is_file():
        if not root.name.startswith("."):
            yield root
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            yield Path(dirpath) / name


READ_AHEAD = 32  # files whose fingerprints are read at once (the NAS answers slowly, not busy)
READ_THREADS = 6


def _batches(paths, size: int):
    batch: list[Path] = []
    for path in paths:
        batch.append(path)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def _fingerprints(session: Session, batch: list[Path]) -> dict[Path, str]:
    """Fingerprints of the new media files of a batch, read in parallel. Files that are known
    already, or cannot be read, are left out (the registration handles them)."""
    from mam.helpers.local_db import models as m

    todo = [p for p in batch if media_kind(p) is not None]
    if not todo:
        return {}
    known = set()
    for i in range(0, len(todo), 500):
        chunk = [str(p.resolve()) for p in todo[i : i + 500]]
        known |= set(
            session.scalars(
                select(m.MediaFile.original_path).where(m.MediaFile.original_path.in_(chunk))
            )
        )
        known |= set(session.scalars(select(m.FileAlias.path).where(m.FileAlias.path.in_(chunk))))
    new = [p for p in todo if str(p.resolve()) not in known]
    out: dict[Path, str] = {}

    def read(path: Path) -> None:
        with contextlib.suppress(OSError):  # the registration reports unreadable files
            out[path] = fingerprint(path)

    if len(new) > 1:
        with ThreadPoolExecutor(READ_THREADS) as pool:
            list(pool.map(read, new))
    return out


def scan_folder(session: Session, root: Path, commit_every: int = 50) -> ScanResult:
    """Register every audio/video file below `root`. Other files are ignored."""
    from mam.helpers.local_db import models as m

    result = ScanResult(root=str(root))
    known_ids = {i for (i,) in session.query(m.MediaFile.id).all()}
    ignored: dict[str, list[str]] = defaultdict(list)
    dupes: dict[str, list[dict]] = defaultdict(list)
    listed_ignored = listed_dupes = 0

    def paths():
        for batch in _batches(iter_media(root), READ_AHEAD):
            prints.update(_fingerprints(session, batch))
            yield from batch

    prints: dict[Path, str] = {}
    for path in paths():
        if media_kind(path) is None:
            result.ignored += 1
            if listed_ignored < MAX_LISTED:
                ignored[path.suffix.lower() or "(no extension)"].append(_rel(path, root))
                listed_ignored += 1
            continue
        result.found += 1
        try:
            record = register_original(session, path, prints.pop(path, None))
            if record is None:
                continue
            if record.original_path != str(path.resolve()):
                result.duplicates += 1  # byte-identical to a file registered elsewhere
                if listed_dupes < MAX_LISTED:
                    rel = _rel(path, root)
                    folder, _, name = rel.rpartition("/")
                    dupes[folder or "(top folder)"].append(
                        {"name": name, "of": record.original_path}
                    )
                    listed_dupes += 1
            elif record.id in known_ids:
                result.known += 1
            else:
                known_ids.add(record.id)
                result.new += 1
        except OSError as exc:
            result.errors += 1
            log.warning("Cannot read %s: %s", path, exc)
        if result.found % commit_every == 0:
            session.commit()
    session.commit()
    result.details_truncated = result.ignored > listed_ignored or result.duplicates > listed_dupes
    result.ignored_by_type = [
        {"ext": ext, "count": len(files), "files": files}
        for ext, files in sorted(ignored.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    ]
    result.duplicates_by_folder = [
        {"folder": folder, "items": items} for folder, items in sorted(dupes.items())
    ]
    log.info("Scanned %s: %s", root, (result.root, result.found, result.new, result.known))
    return result
