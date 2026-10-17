"""Tool registry: what the action menu can run, on which worker pool, and in what order."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from mam.config import Settings
from mam.helpers.local_db import models as m

# Full processing order (CONTRACTS.md). Tools not built yet are skipped by the pipeline.
PIPELINE_ORDER = [
    "renamer",
    "kirtan-splitter",
    "class-discoverer",
    "content-discoverer",
    "class-trimmer",
]


@dataclass
class JobContext:
    job_id: int
    session: Session
    settings: Settings
    dry_run: bool
    params: dict[str, Any]
    media_file: m.MediaFile | None = None


@dataclass
class ToolOutcome:
    """What a tool reports back. `state` drives the file status."""

    state: str  # done | review | skipped | corrupted | info
    summary: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    label: str
    pool: str  # ml | cpu | io
    run: Callable[[JobContext], ToolOutcome]
    description: str
    per_file: bool = True
    changes_files: bool = True
    sets_status: bool = True  # read-only checks leave the processing status alone


def _verify(ctx: JobContext) -> ToolOutcome:
    from mam.errors import CorruptedMedia
    from mam.helpers.audio_optimizer import probe, verify_decode

    path = Path(ctx.media_file.original_path or ctx.media_file.processed_path)
    try:
        info = probe(path)
    except CorruptedMedia as exc:
        return ToolOutcome("corrupted", str(exc))
    errors = verify_decode(path)
    if errors:
        return ToolOutcome("corrupted", f"{len(errors)} decode errors", {"errors": errors[:20]})
    return ToolOutcome(
        "info",
        f"{info.codec} {round((info.bitrate or 0) / 1000)} kbps, {info.duration / 60:.1f} min",
        {"codec": info.codec, "bitrate": info.bitrate, "duration": info.duration},
    )


def _measure(ctx: JobContext) -> ToolOutcome:
    from mam.helpers.audio_optimizer import measure

    path = Path(ctx.media_file.processed_path or ctx.media_file.original_path)
    loud = measure(path)
    meta = dict(ctx.media_file.meta or {})
    meta["loudness"] = {"integrated": loud.integrated, "true_peak": loud.true_peak, "lra": loud.lra}
    ctx.media_file.meta = meta
    return ToolOutcome(
        "info",
        f"{loud.integrated:.1f} LUFS, peak {loud.true_peak:.1f} dBTP, LRA {loud.lra:.1f} LU",
        meta["loudness"],
    )


def _scan(ctx: JobContext) -> ToolOutcome:
    from mam.manager.scanner import scan_folder

    result = scan_folder(ctx.session, Path(ctx.params["path"]))
    return ToolOutcome(
        "info",
        f"{result.found} media files: {result.new} new, {result.known} known, "
        f"{result.duplicates} identical copies, {result.ignored} other files ignored",
        result.to_dict(),
    )


def _renamer(ctx: JobContext) -> ToolOutcome:
    from mam.tools.renamer.tool import run_renamer

    return run_renamer(ctx)


TOOLS: dict[str, ToolSpec] = {
    "renamer": ToolSpec(
        "renamer",
        "Renamer",
        "io",
        _renamer,
        "Find the date, WHAT and WHERE, rename and copy to processed-files",
    ),
    "verify": ToolSpec(
        "verify",
        "Verify",
        "cpu",
        _verify,
        "Decode the whole file to detect corruption",
        changes_files=False,
        sets_status=False,
    ),
    "measure": ToolSpec(
        "measure",
        "Measure loudness",
        "cpu",
        _measure,
        "Measure integrated loudness and true peak",
        changes_files=False,
        sets_status=False,
    ),
    "scan": ToolSpec(
        "scan",
        "Scan folder",
        "io",
        _scan,
        "Register media files in a folder",
        per_file=False,
        changes_files=False,
        sets_status=False,
    ),
}


def menu_tools() -> list[ToolSpec]:
    return [t for t in TOOLS.values() if t.per_file]


def next_in_pipeline(tool: str) -> str | None:
    if tool not in PIPELINE_ORDER:
        return None
    for name in PIPELINE_ORDER[PIPELINE_ORDER.index(tool) + 1 :]:
        if name in TOOLS:
            return name
    return None


def pipeline_start() -> str:
    return next(name for name in PIPELINE_ORDER if name in TOOLS)
