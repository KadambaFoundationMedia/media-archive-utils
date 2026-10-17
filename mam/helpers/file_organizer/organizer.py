"""Copy or move files into processed/<category-folder>/ (see helpers/file-organizer/SPECS.md).

Rules (SPEC.md section 2):
- Originals are copied, never moved. Processed copies may be moved between folders.
- No WHAT (or no matching category) -> processed/_unresolved/.
- Same final name + same size -> saved with a `_duplicate` suffix.
- Same final name + different size -> `-2`, `-3`, ... before the extension, flagged for review.
"""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from mam.config import Settings, get_settings
from mam.errors import ReadOnlyViolation
from mam.helpers.file_organizer.categories import CategoryMatch, CategoryMatcher
from mam.helpers.local_db.db import session_scope
from mam.helpers.local_db.repo import record_event

log = logging.getLogger(__name__)

UNRESOLVED = "_unresolved"


@dataclass
class OrganizeResult:
    src: Path
    dst: Path
    action: str  # copy | move | none
    dry_run: bool
    category: str | None
    duplicate: bool = False
    clash: bool = False  # a different file had the same name; needs review

    @property
    def needs_review(self) -> bool:
        return self.clash


def _is_below(path: Path, roots: list[Path]) -> bool:
    resolved = path.resolve()
    return any(resolved == r or r in resolved.parents for r in roots)


def assert_writable(path: Path, settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    if _is_below(path, settings.read_only_roots):
        raise ReadOnlyViolation(f"Refusing to write below an originals folder: {path}")


def _atomic_copy(src: Path, dst: Path) -> None:
    """Copy via a temporary name so a crash never leaves a partial file at `dst`."""
    tmp = dst.with_name(f".{dst.name}.part")
    try:
        with src.open("rb") as fin, tmp.open("wb") as fout:
            shutil.copyfileobj(fin, fout, length=8 * 1024 * 1024)
            fout.flush()
            os.fsync(fout.fileno())
        shutil.copystat(src, tmp)
        os.replace(tmp, dst)
    finally:
        tmp.unlink(missing_ok=True)


def _move(src: Path, dst: Path) -> None:
    try:
        os.replace(src, dst)
    except OSError:  # different filesystem
        _atomic_copy(src, dst)
        src.unlink()


def _with_suffix(name: str, suffix: str) -> str:
    stem, dot, ext = name.rpartition(".")
    return f"{stem}{suffix}.{ext}" if dot else f"{name}{suffix}"


def resolve_destination(folder: Path, name: str, src: Path) -> tuple[Path, bool, bool]:
    """Return (destination, is_duplicate, is_clash) for `name` inside `folder`."""
    dst = folder / name
    if not dst.exists() or dst.resolve() == src.resolve():
        return dst, False, False
    size = src.stat().st_size
    if dst.stat().st_size == size:
        candidate = folder / _with_suffix(name, "_duplicate")
        n = 2
        while candidate.exists():
            candidate = folder / _with_suffix(name, f"_duplicate-{n}")
            n += 1
        return candidate, True, False
    n = 2
    while True:
        candidate = folder / _with_suffix(name, f"-{n}")
        if not candidate.exists():
            return candidate, False, True
        if candidate.stat().st_size == size:
            return folder / _with_suffix(candidate.name, "_duplicate"), True, True
        n += 1


class FileOrganizer:
    def __init__(self, matcher: CategoryMatcher, settings: Settings | None = None) -> None:
        self.matcher = matcher
        self.settings = settings or get_settings()

    @property
    def root(self) -> Path:
        return self.settings.media_processed_files_path

    def target_folder(self, what: str | None) -> tuple[Path, CategoryMatch | None]:
        match = self.matcher.match(what)
        folder = match.category.folder if match else UNRESOLVED
        return self.root / folder, match

    def organize(
        self,
        src: Path,
        new_name: str,
        what: str | None,
        *,
        tool: str,
        dry_run: bool = True,
        media_file_id: int | None = None,
    ) -> OrganizeResult:
        folder, match = self.target_folder(what)
        assert_writable(folder, self.settings)
        # Anything outside the processed root is treated as an original and only copied.
        is_original = not _is_below(src, [self.root.resolve()])
        dst, duplicate, clash = resolve_destination(folder, new_name, src)
        if dst.resolve() == src.resolve():
            action = "none"
        else:
            action = "copy" if is_original else "move"
        result = OrganizeResult(
            src=src,
            dst=dst,
            action=action,
            dry_run=dry_run,
            category=match.category.name if match else None,
            duplicate=duplicate,
            clash=clash,
        )
        if not dry_run and action != "none":
            folder.mkdir(parents=True, exist_ok=True)
            if action == "copy":
                _atomic_copy(src, dst)
            else:
                _move(src, dst)
        with session_scope() as session:
            record_event(
                session,
                action=action,
                tool=tool,
                dry_run=dry_run,
                media_file_id=media_file_id,
                src=src,
                dst=dst,
                detail={"category": result.category, "duplicate": duplicate, "clash": clash},
            )
        log.info(
            "%s%s %s -> %s",
            action,
            " (dry-run)" if dry_run else "",
            src.name,
            dst.relative_to(self.root) if dst.is_relative_to(self.root) else dst,
        )
        return result
