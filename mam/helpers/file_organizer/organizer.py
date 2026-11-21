"""Copy or move files into processed/<category-folder>/ (see helpers/file-organizer/SPECS.md).

Rules (SPEC.md section 2):
- Originals are copied, never moved, unless the Renamer's "Move files instead of copying" option
  is on (decided 2026-11-21). Then the file is moved with a verified copy: the original is only
  removed after the new file has been read back and matches. Processed copies may be moved between
  folders.
- With the Renamer's postfix option the original left behind by a copy is renamed with the
  postfix (`name_processed.mp3`) to mark it done.
- No WHAT (or no matching category) -> processed/_unresolved/.
- Same final name + same size -> saved with a `_duplicate` suffix.
- Same final name + different size -> `-2`, `-3`, ... before the extension, flagged for review.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from mam.config import Settings, get_settings
from mam.errors import MamError, ReadOnlyViolation
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
    moved_original: bool = False  # the original itself was (or would be) moved, not copied
    marked_original: Path | None = None  # new name of the original left behind (postfix)
    warnings: list[str] = field(default_factory=list)

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


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_copy(src: Path, dst: Path, *, verify: bool = False) -> None:
    """Copy via a temporary name so a crash never leaves a partial file at `dst`.

    With `verify` the copy is read back and compared (size and SHA-256 of the source, taken while
    copying) before it gets its final name; a mismatch raises and leaves nothing at `dst`.
    """
    tmp = dst.with_name(f".{dst.name}.part")
    digest = hashlib.sha256() if verify else None
    try:
        with src.open("rb") as fin, tmp.open("wb") as fout:
            while chunk := fin.read(8 * 1024 * 1024):
                fout.write(chunk)
                if digest:
                    digest.update(chunk)
            fout.flush()
            os.fsync(fout.fileno())
        shutil.copystat(src, tmp)
        if tmp.stat().st_size != src.stat().st_size:
            raise MamError(
                f"Copy of {src.name} is {tmp.stat().st_size} bytes, expected the same size"
            )
        if digest and _hash_file(tmp) != digest.hexdigest():
            raise MamError(f"Copy of {src.name} does not match the source after reading it back")
        os.replace(tmp, dst)
    finally:
        tmp.unlink(missing_ok=True)


def _move(src: Path, dst: Path) -> bool:
    """Move `src` to `dst` without ever losing data. Returns True when `src` is gone.

    Same volume: one atomic rename. Another volume (the NAS to a local disk): a verified copy
    (see `_atomic_copy`), and the source is only removed after that. If the source cannot be
    removed (read-only share, permissions) the new file stays, the source stays too, and False
    is returned so the caller can say so.
    """
    if dst.exists():
        raise MamError(f"{dst} already exists; not overwriting it")
    try:
        os.rename(src, dst)
        return True
    except OSError:
        if not src.exists():
            raise  # nothing to move
        # Another volume or a refused rename: fall through to the verified copy.
    _atomic_copy(src, dst, verify=True)
    try:
        src.unlink()
    except OSError as exc:
        log.warning("Copied %s but could not remove the source: %s", src, exc)
        return False
    return True


def mark_original(src: Path, postfix: str) -> Path | None:
    """Rename the original left behind with `postfix` before its extension.

    Returns the new path, or None if the name already has the postfix. Never overwrites: an
    existing name gets `-2`, `-3`, ... Raises OSError if the rename is not allowed.
    """
    stem = src.stem
    if not postfix or stem.endswith(postfix):
        return None
    target = src.with_name(_with_suffix(src.name, postfix))
    n = 2
    while target.exists():
        target = src.with_name(_with_suffix(src.name, f"{postfix}-{n}"))
        n += 1
    os.rename(src, target)
    return target


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

    def target_folder(
        self, what: str | None, category: str | None = None
    ) -> tuple[Path, CategoryMatch | None]:
        """Folder for a WHAT; a known `category` name wins when the WHAT does not match."""
        match = self.matcher.match(what)
        if match is None and category:
            found = self.matcher.by_name(category)
            match = CategoryMatch(found, category, 0) if found else None
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
        category: str | None = None,
        move_original: bool = False,
        postfix: str = "",
    ) -> OrganizeResult:
        folder, match = self.target_folder(what, category)
        assert_writable(folder, self.settings)
        # Anything outside the processed root is treated as an original and only copied.
        is_original = not _is_below(src, [self.root.resolve()])
        dst, duplicate, clash = resolve_destination(folder, new_name, src)
        moving_original = move_original and is_original
        if dst.resolve() == src.resolve():
            action = "none"
        else:
            action = "move" if (moving_original or not is_original) else "copy"
        result = OrganizeResult(
            src=src,
            dst=dst,
            action=action,
            dry_run=dry_run,
            category=match.category.name if match else None,
            duplicate=duplicate,
            clash=clash,
            moved_original=moving_original and action == "move",
        )
        # The postfix marks the original that a copy leaves behind; a move leaves nothing.
        marks = (
            bool(postfix) and is_original and action == "copy" and not src.stem.endswith(postfix)
        )
        if marks:
            result.marked_original = src.with_name(_with_suffix(src.name, postfix))
        if not dry_run and action != "none":
            folder.mkdir(parents=True, exist_ok=True)
            if action == "copy":
                _atomic_copy(src, dst)
                if marks:
                    self._mark(src, postfix, result, tool, media_file_id)
            elif is_original:
                if not _move(src, dst):
                    result.warnings.append(
                        f"Moved to {dst.name}, but the original {src} could not be removed."
                    )
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
                detail={
                    "category": result.category,
                    "duplicate": duplicate,
                    "clash": clash,
                    "original_moved": result.moved_original,
                },
            )
        log.info(
            "%s%s %s -> %s",
            action,
            " (dry-run)" if dry_run else "",
            src.name,
            dst.relative_to(self.root) if dst.is_relative_to(self.root) else dst,
        )
        return result

    def _mark(
        self, src: Path, postfix: str, result: OrganizeResult, tool: str, media_file_id: int | None
    ) -> None:
        """Rename the original with the postfix; a refusal is a warning, the copy is kept."""
        try:
            new = mark_original(src, postfix)
        except OSError as exc:
            result.marked_original = None
            result.warnings.append(f"Copied, but the original could not be renamed: {exc}")
            return
        result.marked_original = new
        if new is None:
            return
        with session_scope() as session:
            record_event(
                session,
                action="mark_original",
                tool=tool,
                dry_run=False,
                media_file_id=media_file_id,
                src=src,
                dst=new,
                detail={"postfix": postfix},
            )
