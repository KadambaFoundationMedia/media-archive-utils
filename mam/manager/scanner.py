"""Register original media files found below a folder."""

from __future__ import annotations

import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from sqlalchemy.orm import Session

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

    def to_dict(self) -> dict:
        return asdict(self)


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


def scan_folder(session: Session, root: Path, commit_every: int = 50) -> ScanResult:
    """Register every audio/video file below `root`. Other files are ignored."""
    from mam.helpers.local_db import models as m

    result = ScanResult(root=str(root))
    known_ids = {i for (i,) in session.query(m.MediaFile.id).all()}
    for path in iter_media(root):
        if media_kind(path) is None:
            result.ignored += 1
            continue
        result.found += 1
        try:
            record = register_original(session, path)
            if record is None:
                continue
            if record.original_path != str(path.resolve()):
                result.duplicates += 1  # byte-identical to a file registered elsewhere
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
    log.info("Scanned %s: %s", root, result.to_dict())
    return result
