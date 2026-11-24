"""Whole-recording gain (encode #1): bring the class to -18 LUFS +/-1 LU before transcribing."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from mam.config import Settings
from mam.helpers.audio_optimizer import measure, normalize_whole
from mam.helpers.local_db import models as m
from mam.helpers.local_db.hashing import fingerprint, sha256
from mam.helpers.local_db.repo import media_kind, record_event

log = logging.getLogger(__name__)
TOOL = "class-discoverer"


def apply_gain(
    session: Session, record: m.MediaFile, audio: Path, settings: Settings, *, dry_run: bool
) -> dict[str, Any]:
    """Normalize the processed copy in place (one re-encode at the source format).

    A recording that already meets the target is left alone: no needless re-encode.
    """
    if media_kind(audio) == "video":
        # a video file is never edited (SPEC.md section 4); its sound is only read
        return {"applied": False, "reason": "video file: never edited"}
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
