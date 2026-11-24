"""Whole-recording gain (encode #1): bring the class to -18 LUFS +/-1 LU before transcribing."""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from mam.config import Settings
from mam.helpers.audio_optimizer import measure, normalize_whole
from mam.helpers.local_db import models as m
from mam.helpers.local_db.hashing import fingerprint, sha256
from mam.helpers.local_db.repo import record_event

log = logging.getLogger(__name__)
TOOL = "class-discoverer"

_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


@contextmanager
def file_lock(audio: Path) -> Iterator[None]:
    """One gain at a time per file. The gain runs as its own CPU job (`audio-gain`) ahead of the
    class discoverer, which waits here when it reaches the file first. Re-entrant, so a job can
    hold the lock around `apply_gain` and its own database commit."""
    with _LOCKS_GUARD:
        lock = _LOCKS.setdefault(str(audio.resolve()), threading.RLock())
    with lock:
        yield


def apply_gain(
    session: Session, record: m.MediaFile, audio: Path, settings: Settings, *, dry_run: bool
) -> dict[str, Any]:
    """Normalize the processed copy in place (one re-encode at the source format).

    A recording that already meets the target is left alone: no needless re-encode.
    """
    with file_lock(audio):
        return _apply_gain(session, record, audio, settings, dry_run=dry_run)


def _apply_gain(
    session: Session, record: m.MediaFile, audio: Path, settings: Settings, *, dry_run: bool
) -> dict[str, Any]:
    before = measure(audio)
    if before.meets_target():
        return {"applied": False, "before": before.integrated, "reason": "already at the target"}
    tmp = audio.with_name(f".{audio.stem}.gain{audio.suffix}")
    result = normalize_whole(audio, tmp, dry_run=dry_run)
    info = {
        "applied": not dry_run,
        "before": result.before.integrated,
        "gain_db": result.gain_db,
        "mode": result.mode,  # linear | dynamic (a limiter was needed)
        "encoder": result.encoder,
    }
    if dry_run:
        return info
    try:
        info["after"] = result.after.integrated if result.after else None
        info["true_peak"] = result.after.true_peak if result.after else None
        info["qa_passed"] = result.qa_passed
        os.replace(tmp, audio)
    finally:
        tmp.unlink(missing_ok=True)
    stat = audio.stat()
    record.size, record.mtime = stat.st_size, stat.st_mtime
    record.fingerprint = fingerprint(audio)
    record.sha256 = sha256(audio)
    record_event(
        session,
        action="write",
        tool=TOOL,
        dry_run=False,
        media_file_id=record.id,
        src=audio,
        dst=audio,
        detail={"gain": info},
    )
    return info


def run_audio_gain(ctx) -> ToolOutcome:  # noqa: F821 - imported lazily to avoid a cycle
    """The gain step on its own, in the CPU pool, so the files of a batch are evened out in
    parallel while the ML worker transcribes another file. The class discoverer runs the same
    step again: for a file that is at the target by then it only measures."""
    from mam.manager.tools import ToolOutcome
    from mam.tools.class_discoverer.tool import not_a_class, source_audio

    record = ctx.media_file
    if record is None or record.status in ("Excluded", "Deleted"):
        return ToolOutcome("skipped", "The file is not processed any more", {})
    skip = not_a_class(record)
    if skip:
        return ToolOutcome("skipped", skip, {})
    try:
        audio = source_audio(record, dry_run=False)
    except Exception as exc:  # noqa: BLE001 - the class discoverer reports it to the person
        return ToolOutcome("skipped", str(exc), {})
    with file_lock(audio):
        info = apply_gain(ctx.session, record, audio, ctx.settings, dry_run=False)
        if info.get("applied"):
            meta = dict(record.meta or {})
            meta["gain_pre"] = info  # the class discoverer reports it with its own result
            record.meta = meta
        ctx.session.commit()  # before the lock goes: the class discoverer reads the new hash
    summary = "Volume evened out" if info.get("applied") else "Volume already at the target"
    return ToolOutcome("info", summary, {"gain": info})
