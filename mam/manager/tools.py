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


def _kirtan_splitter(ctx: JobContext) -> ToolOutcome:
    from mam.tools.kirtan_splitter.tool import run_kirtan_splitter

    return run_kirtan_splitter(ctx)


TOOLS: dict[str, ToolSpec] = {
    "renamer": ToolSpec(
        "renamer",
        "Renamer",
        "io",
        _renamer,
        "Find the date, WHAT and WHERE, rename and copy to processed-files",
    ),
    "kirtan-splitter": ToolSpec(
        "kirtan-splitter",
        "Kirtan splitter",
        "ml",
        _kirtan_splitter,
        "Find where the kirtan ends and split a combined recording into kirtan and class",
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
